#!/usr/bin/env python

import os
import sys
import io
import tempfile
import logging
import itertools
import time
import traceback
from threading import Thread
from Queue import Queue, Empty

from subprocess import Popen, PIPE
from ycmd.completers.completer import Completer
from ycmd import responses
from ycmd import utils

_logger = logging.getLogger(__name__)

def log(level, msg):
    _logger.log(level, '[dcdcompl] %s' % msg)

def error(msg):
    log(logging.ERROR, msg)

def warning(msg):
    log(logging.WARNING, msg)

def info(msg):
    log(logging.INFO, msg)

def debug(msg):
    log(logging.DEBUG, msg)

class DCDCompleter(Completer):
    def __init__(self, user_options):
        super(DCDCompleter, self).__init__(user_options)
        self._popener = utils.SafePopen
        self._binary = utils.PathToFirstExistingExecutable(['dcd-client'])

        if not self._binary:
            msg = "Couldn't find dcd-client binary. Is it in the path?"
            error(msg)
            raise RuntimeError(msg)

        info('DCD completer loaded')

    def SupportedFiletypes(self):
        return set(['d'])

    def ShouldUseNowInner(self, request_data):
        shouldUseNowInner = len(self.ComputeCandidates(request_data)) > 0
        return shouldUseNowInner

    def ComputeCandidates(self, request_data):
        filepath = request_data['filepath']
        linenum = request_data['line_num']
        colnum = request_data['column_num']
        contents = utils.ToUtf8IfNeeded(
            request_data['file_data'][filepath]['contents'])
        try:
            return [sug for sug in self._Suggest(filepath, linenum, colnum, contents) if sug]
        except:
            error(traceback.format_exc())
            return []

    def _EmptyQueue(self):
        while self._dataqueue.not_empty:
            try:
                self._dataqueue.get_nowait()
            except:
                break

    def _Suggest(self, filename, linenum, column, contents):
        dirtyfile = None
        if contents:
            dirtyfile, dirtyfilename = tempfile.mkstemp()
            os.write(dirtyfile, contents)
            os.close(dirtyfile)
        else:
            with open(filename, 'r') as f:
                contents = f.read()
        cursorPos = self.getCursorPos(linenum, column, contents) - 1
        filename = filename if not dirtyfile else dirtyfilename
        try:
            completionData = self._ExecClient('-c %d' % cursorPos, filename)
            if completionData[1]:
                error('Completion error from dcd-client:\n' + completionData[1])
                return []

            completions = [self._CreateCompletionData(line, contents, cursorPos, filename)
                    for line in completionData[0].splitlines()
                    if not line.strip() in ['identifiers', '']]
            return completions
        except KeyboardInterrupt:
            pass
        finally:
            if dirtyfile:
                os.unlink(dirtyfilename)
        return []

    def getCursorPos(self, linenum, column, contents):
        endingsLength = linenum if contents.find('\r\n') < 0 else linenum * 2
        return len(''.join(contents.splitlines()[:linenum-1])) + endingsLength + column - 1

    def _ExecClient(self, cmd, filename):
        cmd += ' ' + filename
        args = [self._binary] + cmd.split(' ')
        popen = self._popener(args, executable = self._binary,
                stdin = PIPE, stdout = PIPE, stderr = PIPE)
        return popen.communicate()

    def _CreateCompletionData(self, line, contents, cursorPos, filename):
        if line.find('\t') < 0:
            return []
        name, kind = line.split('\t')

        imports = self.getImports(contents)
        docText = self.getDocText(name, imports)

        longname = name
        if '.' in name:
            name = name.split('.')[-1]
            longname = name + ' (' + longname + ')'

        return responses.BuildCompletionData(
                insertion_text = name,
                menu_text = longname,
                kind = kind,
                detailed_info = '%s: %s\n%s' %
                    (name, kind, docText
                        .replace('\\n', '\n'))
                )

    def getImports(self, contents):
        return '\n'.join(
            [line for line in contents.splitlines()
            if line.startswith('import') and line.strip().endswith(';')])

    def getDocText(self, symbol, imports):
        tmpfile, tmpfilename  = tempfile.mkstemp()
        try:
            text = imports + '\n'
            text += symbol
            cursorPos = len(text)
            os.write(tmpfile, text)
            os.close(tmpfile)

            docData = self._ExecClient('-d -c %d' % (cursorPos - 1), tmpfilename)
            if docData[1]:
                error('Doc error from dcd-client:\n' + docData[1])
            else:
                docText = docData[0]
            return docText
        finally:
            os.unlink(tmpfilename)
        return ''
