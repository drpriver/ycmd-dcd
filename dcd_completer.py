#!/usr/bin/env python3

import os
import sys
import io
import tempfile
import logging
import itertools
import time
import traceback
import re
from threading import Thread
from queue import Queue, Empty

from subprocess import Popen, PIPE
from ycmd.completers.completer import Completer
from ycmd import responses
from ycmd import utils

IncludeSymbolFilename = True

_logger = logging.getLogger(__name__)

def log(level, msg, *args):
    _logger.log(level, '[dcdcompl] '+ msg, *args)

def error(msg, *args):
    log(logging.ERROR, msg, *args)

def warning(msg, *args):
    log(logging.WARNING, msg, *args)

def info(msg, *args):
    log(logging.INFO, msg, *args)

def debug(msg, *args):
    log(logging.DEBUG, msg, *args)

class DCDCompleter(Completer):
    newline_re = re.compile(r'([^\\])\\n')

    def __init__(self, user_options):
        super().__init__(user_options)
        self._popener = utils.SafePopen
        self._binary = utils.PathToFirstExistingExecutable(['dcd-client'])

        if not self._binary:
            msg = "Couldn't find dcd-client binary. Is it in the path?"
            error(msg)
            raise RuntimeError(msg)

        info('DCD completer loaded')

    # override
    def SupportedFiletypes(self):
        return {'d'}

    # override
    def ShouldUseNowInner(self, request_data):
        return len(self.ComputeCandidates(request_data)) > 0

    # override
    def ComputeCandidates(self, request_data):
        filepath = request_data['filepath']
        linenum = request_data['line_num']
        colnum = request_data['column_num']
        contents = request_data['file_data'][filepath]['contents']
        try:
            return [sug for sug in self._suggest(filepath, linenum, colnum, contents) if sug]
        except:
            error(traceback.format_exc())
            return []

    # override
    def GetSubcommandsMap(self):
        return {
            'GoTo': self.__class__._goto,
            'GoToDefinition': self.__class__._goto,
            'GoToDeclaration': self.__class__._goto,
        }

    def _suggest(self, filename, linenum, column, contents):
        if not contents:
            with open(filename, 'r') as f:
                contents = f.read()
        cursorPos = self.getCursorPos(linenum, column, contents) - 1
        try:
            completionData = self._ExecClient('-c %d' % cursorPos, contents)
            if completionData[1]:
                error('Completion error from dcd-client:\n' + completionData[1].decode('utf-8'))
                return []
            EXCLUDES = frozenset({
                'identifiers',
                '',
                'stringof\tk',
                'mangleof\tk',
                'tupleof\tk',
                'alignof\tk',
                'init\tk',
                'sizeof\tk',
                'destroy\tF',
                'hashOf\tF',
                'opEquals\tF',
                'toString\tF',
                'toHash\tF',
                'setSameMutex\tF',
                'opCmp\tF',
            })

            completions = [self._create_completion_data(line, contents)
                    for line in completionData[0].decode('utf-8').splitlines()
                    if not line.startswith('_') and line.strip() not in EXCLUDES]
            return completions
        except KeyboardInterrupt:
            pass
        return []

    def _get_cursor_pos(self, linenum, column, contents):
        endingsLength = linenum if contents.find('\r\n') < 0 else linenum * 2
        return len(''.join(contents.splitlines()[:linenum-1])) + endingsLength + column - 1

    def _exec_client(self, cmd, contents):
        args = [self._binary] + cmd.split(' ')
        popen = self._popener(args, executable = self._binary,
                stdin = PIPE, stdout = PIPE, stderr = PIPE)
        return popen.communicate(contents.encode('utf-8'))

    def _create_completion_data(self, line, contents):
        if '\t' not in line:
            return []
        name, kind = line.split('\t')

        longname = name
        if '.' in name:
            name = name.split('.')[-1]
            longname = name + ' (' + longname + ')'

        return responses.BuildCompletionData(
            insertion_text = name,
            menu_text = longname,
            kind = kind,
            detailed_info = '',
        )


    def _goto(self, request_data, args):
        filepath = request_data['filepath']
        linenum = request_data['line_num']
        colnum = request_data['column_num']
        contents = request_data['file_data'][filepath]['contents']
        cursor = self._get_cursor_pos(linenum, colnum, contents)
        data = self._exec_client(f'-l -c {cursor}', contents)[0]
        d = data.decode('utf-8').strip()
        if not d: return None

        gotos = []
        for line in d.split('\n'):
            if '\t' not in line:
                continue
            f, b = line.split('\t')
            b = int(b)
            if f == 'stdin':
                f = filepath
            else:
                with open(f, 'r') as fp:
                    contents = fp.read()
            l = contents[0:b].count('\n')+1
            c = ''.join(reversed(contents[0:b])).find('\n')+1
            gotos.append(responses.BuildGoToResponse(f, l, c, ''))
        if len(gotos) == 1:
            return gotos[0]
        return gotos if gotos else None


