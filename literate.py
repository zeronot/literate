# -*- coding: utf-8 -*-
"""
==============
Literate
==============


:Author: `EnricoGiampieri <https://github.com/EnricoGiampieri>`_
:Version number: 0.1
:github: `EnricoGiampieri/literate <https://github.com/EnricoGiampieri/literate>`_

.. contents:: :depth: 2

Introduction
================

Literate is a pure python library to create reports out of python scripts.

It takes the target script and perform the following steps:

* divides it into self contained blocks (one statement each)
* transforms the block composed of only strings into ReStructured Text
* execute the others in a sandbox environment that capture as much of the output as possible
* compile into Restructured Text the executed block joining them with the their output. This include figures generated by pylab and matplotlib, that are saved as static files.
* extract docstring inside blocks (like definition of classes and functions, but also for loops, if-else, etc... basically anything that ends with a colon and starts a new block).
* join everything back together in a single rst document
* compile the rst in an html form for easier reading.


"""

# %%
from contextlib import contextmanager
from docutils.core import publish_parts
from io import StringIO, BytesIO
from itertools import groupby, dropwhile, accumulate, takewhile
import os
import sys
import tokenize

"""
Pylab Cage
==============

One of the main features of literate is the cage that capture and record
all the output from the program. right now what is captured is

* stdout printing
* stderr printing
* pylab/matplotlib show calls


All this functions are performed by the OutputCage class.
This class is used with a single instance for each execution.

It generates a context where the sys streams are redirected and the pylab show
call rerouted to the instance itself, that records the figure not already shown
and generates them for the appropriate block to retrieve
in the execution phase.
"""


# %%
# this wrap the show function and keeps track of the last created plot
class OutputCage(object):
    """replace and register sys.sdtout, sys.sdterr, pylab.show and figure.show

    It creates a context where all the most common output are replaced.
    The output toward sys.stdout and sys.stderr are redirected toward a
    StringIO that are then passed out as context variables.

    Pylab function calls will be redirected to itself, and the effect
    will be to write the figure to a BytesIO objects list with all
    the figures that have not been showed up to now.

    This behavior is not completely true to the matplotlib one.
    """
    def __init__(self):
        """creates the object, no parameters are required.

        For a single compilation run only a single object is required.
        """
        self.fig_index = set()
        self.last_drawn = []
        self.old_stdout = sys.__dict__['stdout']
        self.old_stderr = sys.__dict__['stderr']
        self.my_stdout = StringIO()
        self.my_stdout_old = StringIO()
        self.my_stderr = StringIO()
        self.my_stderr_old = StringIO()

    # the output cage: it captures stdout, stderr and pylab figures temporarely
    @contextmanager
    def redifine_output(self, glob):
        """intercept the output to stdout, stderr and the pylab shows.

        Should be used as a context manager, and will give out the
        StringIO that replaces sys.stdout and sis.stderr.
        Pylab shows function results are stored internally to be obtained
        with the :code:`OutputCage.get_figures` function
        """
        # FIXME the problem is that when it does load the stdout
        # and stderr it will create two new StringIO that then
        # are lost in the following call! I have to make them
        # persistent and retrieve the results while it goes

        sys.__dict__['stdout'] = self.my_stdout
        sys.__dict__['stderr'] = self.my_stderr

        pylab_name = "__pylab__literate__"
        exec("import pylab as {}\n".format(pylab_name), glob)
        old_pylab_show = glob[pylab_name].show
        old_figure_show = glob[pylab_name].Figure.show
        old_pyplot_show = glob[pylab_name].matplotlib.pyplot.show

        replaced = {}
        glob[pylab_name].show = self.pylab_show

        def fig_show(fig, *args, **kwargs):
            return self.figure_show(fig, *args, **kwargs)
        glob[pylab_name].Figure.show = fig_show

        glob[pylab_name].matplotlib.pyplot.show = self.pylab_show

        try:
            yield
        finally:
            sys.__dict__['stdout'] = self.old_stdout
            sys.__dict__['stderr'] = self.old_stderr
            glob[pylab_name].show = old_pylab_show
            glob[pylab_name].Figure.show = old_figure_show
            glob[pylab_name].matplotlib.pyplot.show = old_pyplot_show
            for name, value in replaced.items():
                glob[name] = value

    def get_stdout(self):
        str_old = self.my_stdout_old.getvalue()
        str_new = self.my_stdout.getvalue()
        diff_str = str_new[len(str_old):]
        self.my_stdout_old = StringIO(str_new)
        return StringIO(diff_str)

    def get_stderr(self):
        str_old = self.my_stderr_old.getvalue()
        str_new = self.my_stderr.getvalue()
        diff_str = str_new[len(str_old):]
        self.my_stderr_old = StringIO(str_new)
        return StringIO(diff_str)

    def pylab_show(self, *args, **kwargs):
        """this is the replacement of the :code:`pylab.show` function call

        it will get the lastet created figures that are not already shown
        and create binary objects out of them. the results is put in
        a list of BytesIO objects, where each BytesIO is the png (for now)
        representation of the image.

        .. warning::

            This function should be personalized to get options about format
            and resolution, but that is not yet provided
        """
        import pylab
        self.last_drawn = []
        figs = list(map(pylab.figure, pylab.get_fignums()))
        new_figures = [fig for fig in figs if fig not in self.fig_index]
        self.fig_index.update(set(figs))
        # fig = pylab.gcf()
        for fig in new_figures:
            file_descriptor = BytesIO()
            fig.savefig(file_descriptor, format='png')
            self.last_drawn.append(file_descriptor)

    def figure_show(self, figure, *args, **kwargs):
        """this figure is called when a single figure requires a show

        it will show the figure even if it has been show already
        """
        file_descriptor = BytesIO()
        figure.savefig(file_descriptor, format='png')
        self.last_drawn.append(file_descriptor)

    def get_figures(self):
        """this pop the list of all the figures created when pylab.show
        has been called
        """
        res = self.last_drawn
        self.last_drawn = []
        return res

    def generate_globals(self):
        glob = {}
        # correctly handles the __main__ execution
        exec('__name__ = "__main__"', glob)
        exec('import sys', glob)
        exec('sys.argv = {}'.format(repr(argv)), glob)
        # correctly handles the sys.exit call
        exec('def __raises(i):\n\traise KeyboardInterrupt(str(i))')
        exec('sys.exit = __raises')
        exec('del __raises')
        # redirect the matplotlib to the written version
        exec("import matplotlib as __mpl__literate__\n", glob)
        exec("__mpl__literate__.use('Agg')\n", glob)
        exec("del __mpl__literate__", glob)
        return glob

# %%
"""
Helper Functions
================

"""


# DEDENT and INDENT are at the beginning of the line, but maybe after comments
def _evaluate_indent_variation(token_seq):
    """evaluate how much the indentation change with this line of code
    """
    up = sum(token.type == tokenize.INDENT for token in token_seq)
    down = sum(token.type == tokenize.DEDENT for token in token_seq)
    return up-down

_IGNORABLE_TOKENS = [tokenize.INDENT,
                     tokenize.DEDENT,
                     tokenize.COMMENT,
                     tokenize.NEWLINE,
                     tokenize.NL,
                     tokenize.ENCODING,
                     tokenize.ENDMARKER,
                     ]


# %%
def _is_continued_block(token_line):
    """this recognize if the block is an else or similar, that follow
    another block even if it is on the same line.

    This should be a separate function at a certain point...
    """
    for token in token_line:
        if token.type in _IGNORABLE_TOKENS:
            continue
        elif token.type == tokenize.NAME:
            name = token.string
            if name in ['elif', 'else', 'except', 'finally']:
                return True
            else:
                return False
        else:
            return False
    return False


# %%
def _is_docstring(token_line):
    """consider a docstring a string isolated from the rest
    without lines of codes around, but possible with comments.
    return the content or an empty string if invalid.
    if the string is empty, it will not consider it as valid
    """
    is_string = True
    content = ""
    for token in token_line:
        if token.type not in _IGNORABLE_TOKENS + [tokenize.STRING]:
            is_string = False
            break
        elif token.type == tokenize.STRING:
            content += eval(token.string)
    return content if is_string else ""


# %%
def _is_block_start(token_line):
    reversed_line = reversed(token_line)
    for token in reversed_line:
        if token.type in _IGNORABLE_TOKENS:
            continue
        elif token.type == tokenize.OP and token.string == ':':
            return True
        else:
            return False
    return False


# %%
def _equalize_docstring(docstring):
    """this functions should take a docstring, that has the line
    following the first indented, and remove the beginning space common
    to all the lines, leaving the first one unmodified
    """
    lines = list(docstring.splitlines())
    # if there is a null string or a single line
    # no modifications are required
    if len(lines) <= 1:
        return docstring
    first_line = lines[0]
    lines = lines[1:]

    def count_indent(line):
        is_whitespace = lambda c: c in [' ', '\t']
        return len([char for char in takewhile(is_whitespace, line)])

    indents = [count_indent(line) for line in lines if line.strip()]
    min_indent = min(indents) if indents else 0
    lines = [line[min_indent:] if line.strip() else line for line in lines]
    lines = [first_line] + lines
    docstring = "\n".join(lines)
    return docstring


# %%
def _generate_logical_lines(readline):
    """takes a readline from a file and generates a sequence of
    logically complete lines of code.

    it can also take a list of tokens directly.

    """
    # split the lines in tokens
    if callable(readline):
        tokens = tokenize.generate_tokens(readline)
    else:
        tokens = readline

    # NEWLINE is the interruption of a logical line
    # NL is the end of a physical line withuot ending the logical one
    is_complete_line = lambda token: token.type == tokenize.NEWLINE
    res = list(list(l[1]) for l in groupby(tokens, is_complete_line))
    # these are the logical lines, ending with an NL
    lines = [i0+i1 for i0, i1 in zip(res[::2], res[1::2])]
    return lines


# %%
"""
The CodeGroup Class
===================
"""


class CodeGroup(object):
    """this is the main class, responsible for holding the code
    and executing it
    """

    def __init__(self, block_lines, previous_block=None):
        """the input should be a list of lists of tokens

        each list is a logical line"""
        self.tokens = block_lines
        self.previous = previous_block
        self.following = None
        if self.previous is not None:
            self.previous.following = self
        self.results = {}
        self.globals = None

    def get_index(self):
        if self.previous is None:
            return 0
        else:
            return 1+self.previous.get_index()

    @property
    def lines(self):
        return _generate_logical_lines(self.tokens)

    def extract_docstrings(self):
        doc_lines = [bool(_is_docstring(line)) for line in self.lines]
        indexes = range(len(self.lines))
        docstrings = []
        for idx, is_doc, line in zip(indexes, doc_lines, self.lines):
            if is_doc and idx > 0 and not doc_lines[idx-1]:
                prev_line = self.lines[idx-1]
                if not _is_block_start(prev_line):
                    continue
                line_str_pre = str(self.__class__(prev_line))
                line_str = eval(str(self.__class__(line)))
                docstring_text = ".. note::\n\n\t.. code:: python\n\n"

                splitlines = line_str_pre.splitlines()
                splitlines = (line for line in splitlines if line.strip())
                s = "\n".join('\t\t'+line for line in splitlines)
                docstring_text += s + '\n\n'

                line_str = _equalize_docstring(line_str)
                splitlines = line_str.splitlines()
                s = "\n".join('\t'+int_line for int_line in splitlines)
                docstring_text += s + '\n\n'

                docstrings.append(docstring_text.replace('\t', '    '))
        return docstrings

    def __str__(self):
        is_whiteline = lambda s: s == '\\'
        groups_lines = tokenize.untokenize(self.tokens)
        # remove the superfluous lines at the beginning due
        # to how untokenize work join them together again
        groups_lines = dropwhile(is_whiteline, groups_lines.split('\n'))
        groups_lines = list(groups_lines)
        # this final bit is required to assure that the combination
        # of the various groups reconstruct the original source code
        if groups_lines[0] == '\n':
            groups_lines = groups_lines[1:]
        return "\n".join(groups_lines)

    def execute(self, global_dict, pylab_show_cage):
        """execute the block in the given gloabal dict under the given cage
        """
        assert type(global_dict) == dict, "the globals should be a base dict!"
        self.globals = global_dict
        myshow = pylab_show_cage
        do_interrupt = False
        # this is necessary to allow me to keep writing even in the output cage
        with myshow.redifine_output(global_dict):
            # try to capture possible exceptions generated by the code
            # to save them. This could lead to capture external exceptions
            # and save them as results, but I can't see any way out of this
            exceptions = None
            try:
                exec(str(self), global_dict)
            except (KeyboardInterrupt, SystemExit):
                do_interrupt = True
            except Exception as e:
                s = ("On block number {}, with sourcecode:\n'''\n{}'''\n" +
                     " the following exception has been raised:\n")
                s = s.format(self.get_index(), str(self))
                raise type(e)(s + repr(e)).with_traceback(sys.exc_info()[2])
            # take the output results out of the output cage
            out = myshow.get_stdout().getvalue()
            err = myshow.get_stderr().getvalue()

            figures = myshow.get_figures()

            # output to normal lines the global keys, just a debug thing
            # create the result block with the code and all the results
            # and append it to the total array of results
            # each block execution results should be saved in here.
            # it contains the code text, the output, the figures filenames
            # and possible errors
            self.results = {'standard output': out,
                            "standard error": err,
                            "generated figures": figures,
                            "exceptions generated": exceptions,
                            "interrupted": do_interrupt,
                            }
        return self.results

    def has_results(self):
        if not self.results:
            return False
        for key, value in self.results.items():
            if value:
                return True
        return False

    def is_docstring(self):
        """consider a docstring a string isolated from the rest
        without lines of codes around, but possible with comments.
        return the content or an empty string if invalid.
        if the string is empty, it will not consider it as valid
        """
        return _is_docstring(self.tokens)

    def compile(self, output_dir):
        """compile the executed code into rst
        """
        content = self.is_docstring()
        if content:
            return (content + '\n', {})

        compiled_rst = ".. code:: python\n\n"
        indented_lines = ["    "+line for line in str(self).split('\n')]
        compiled_rst += "\n".join(indented_lines)

        for docstring_group in self.extract_docstrings():
            compiled_rst += '\n' + docstring_group + '\n'

        if self.results:  # self.has_results():
            compiled_rst += '\n\n'

        if "standard error" in self.results:
            if self.results["standard error"]:
                compiled_rst += ".. warning::\n\n    ::\n\n"
                for line in self.results["standard error"].split('\n'):
                    compiled_rst += 2*"    "+line+'\n'
        if "exceptions generated" in self.results:
            if self.results["exceptions generated"]:
                compiled_rst += ".. warning:: Exception Raised\n\n    ::\n\n"
                generated = str(self.results["exceptions generated"])
                for line in generated.split('\n'):
                    compiled_rst += 2*"    "+line+'\n'
        if "standard output" in self.results:
            if self.results["standard output"]:
                compiled_rst += "::\n\n"
                for line in self.results["standard output"].split('\n'):
                    compiled_rst += "    "+line+'\n'
        if "generated figures" in self.results:
            figures = self.results["generated figures"]
            figure_dict = {}
            for fig_idx, figure_bytes in enumerate(figures):
                index = self.get_index()
                f_name = "figure_{}_{}.png".format(index, fig_idx)
                figure_dict[f_name] = figure_bytes
                # f_dir = os.path.join(output_dir, f_name)
                # with open(f_dir, 'wb') as file:
                #     file.write(figure_bytes.getvalue())
                f_link = os.path.join(os.path.curdir, f_name)
                compiled_rst += ".. image:: "+str(f_link)+"\n\n"

        return (compiled_rst, figure_dict)

    @classmethod
    def iterate_groups_from_source(cls, readline):
        lines = _generate_logical_lines(readline)
        # for each line, determins its level of variation of indentation
        var_indent_lev = map(_evaluate_indent_variation, lines)
        # accumulate to obtain the total one
        indent_levels = accumulate(var_indent_lev)
        # this checks is the line starts with a decorator
        is_decorator = lambda lg: lg[-1].line.strip().startswith('@')
        last_group = []
        last_created_group = None
        for line, indent_level in zip(lines, indent_levels):
            # if is a flat line, either start or if it is a decorator
            # store it for later
            if indent_level == 0:
                # have to check for decorators
                if (not last_group) or is_decorator(last_group):
                    last_group.extend(line)
                # now I check if the block is the continuation
                # of a previous one
                elif _is_continued_block(line):
                    last_group.extend(line)
                else:
                    new_group = cls(last_group, last_created_group)
                    last_created_group = new_group
                    yield new_group
                    last_group = line.copy()
            # otherwise put it in the current group
            else:
                last_group.extend(line)
        # if the last group is not closed, put it with the others
        if last_group:
            new_group = cls(last_group, last_created_group)
            last_created_group = new_group
            yield new_group


# %%
"""
The Main Function
=================
"""


def run_file(input_file, output_dir, argv):
    with open(input_file) as file:
        origins = file.readline
        groups = CodeGroup.iterate_groups_from_source(origins)
        groups = list(groups)

        pylab_show_cage = OutputCage()
        glob = pylab_show_cage.generate_globals()

        do_execute = True
        for group in groups:
            if do_execute:
                results = group.execute(glob, pylab_show_cage)
                do_execute = not results["interrupted"]

        # close all the obtained figures, as the pylab act as a singleton
        # and stores them. i you launch any code that use pylab after the
        # execution, it will have all the generated figures.
        import pylab
        pylab.close('all')

        # this will be useful in the future, maybe...
        imported_modules = set()
        for key, value in glob.items():
            if type(value) == type(pylab):
                imported_modules.add(value)
        for module in imported_modules:
            pass  # print(module.__name__)

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    f_base = os.path.basename(input_file)
    f_base = os.path.splitext(f_base)[0]
    filename_complete_rst = os.path.join(output_dir, '{}.rst'.format(f_base))
    # compile all the block in rst and get the required figures to save
    compile_results = [group.compile(output_dir) for group in groups]
    # attach all the compiled strings for each block
    compiled_rst = "\n".join(str(piece[0]) for piece in compile_results)
    # saves all the figures as requested by each piece
    for piece in compile_results:
        for f_name, figure_bytes in piece[1].items():
            f_dir = os.path.join(output_dir, f_name)
            with open(f_dir, 'wb') as file:
                file.write(figure_bytes.getvalue())

    with open(filename_complete_rst, 'wt') as rst_file:
        print(compiled_rst, file=rst_file)

    filename_complete_html = os.path.join(output_dir, '{}.html'.format(f_base))
    H = publish_parts(compiled_rst, writer_name='html')['whole']
    with open(filename_complete_html, 'wt') as html_file:
        print(H, file=html_file)
    return True

# %%
"""
Tests
===========
"""

import unittest
source_test_1 = '''
#not docstring
a = 5

#comment 2
"""docstring"""

"""docstring"""

"not docstring"; a = 5
'''

source_test_if_else = '''
if False:
    pass
elif 0:
    pass
else:
    pass
'''

source_test_for_else = '''
for i in range(1):
    pass
else:
    pass
'''

source_test_try_except = '''
try:
    pass
except:
    pass
else:
    pass
finally:
    pass
'''

source_grouping_decorator = '''
#comment
@contextmanager
def function():
    yield 1
'''


source_docstring_extraction = '''
def f():
    "first docstring"
    for i in range(10):
        """second docstring, multiline,
        with additional content

        and a line separation
        """
        print(i)
        "not a docstring"
    for i in range(10):
        "third and last docstring"
        pass
'''

# %%
source_docstring_extraction_with_comments = '''
#comment 1
#comment 2
def f():
    """docstring
    second line

    third line
    """
    pass
#comment
'''

expected_docstring_extraction_with_comments = '''.. note::

    .. code:: python

        #comment 1
        #comment 2
        def f():

    docstring
    second line

    third line
'''

# %%

expected_title_in_warnings = '''.. code:: python

    print('=================', file=sys.stderr)


.. warning::

    ::

        =================

'''


# %%
def _normalize_str(string):
    """removes the trailing white spaces from a multiline string
    It is necessary to confront the results of the printing without
    getting crazy for invisible whitespaces
    """
    string = [line.rstrip() for line in string.splitlines()]
    string = '\n'.join(string)
    return string


# %%
class test_Group(unittest.TestCase):

    def generate_groups(self, source_code):
        """generates the groups from the given source code
        boilerplate code"""
        origin = StringIO(source_code).readline
        groups = CodeGroup.iterate_groups_from_source(origin)
        return groups

    def test_is_docstring_1(self):
        """On the given source code, check which are proper strings that
        will be Weaved out.
        """
        groups = self.generate_groups(source_test_1)
        expected = [False, True, True, False]
        observed = [bool(g.is_docstring()) for g in groups]
        self.assertEqual(expected, observed)

    def test_recomposition_trailing_white_line(self):
        """if the source file has no trailing white line the reconstructed
        source code correspond to the original.
        No garantee if it is not following the proper format

        If it is missing the last newline, it is going to miss the last
        group!
        """
        groups = self.generate_groups(source_test_1)
        generated = "".join(str(g) for g in groups)
        self.assertEqual(generated, source_test_1)

    def test_simple_output(self):
        code = "print(1)\n"
        groups = self.generate_groups(code)
        group0 = list(groups)[0]
        res = group0.execute({}, OutputCage())
        self.assertEqual(res['standard output'], '1\n')
        self.assertEqual(res['standard error'], '')
        self.assertEqual(res['generated figures'], [])
        self.assertEqual(res['exceptions generated'], None)

    def test_simple_exception(self):
        code = "raise ValueError('error')\n"
        groups = self.generate_groups(code)
        group0 = list(groups)[0]
        with self.assertRaises(ValueError):
            group0.execute({}, OutputCage())

    def test_main_section(self):
        code = "if __name__ == '__main__':\n\tprint(5)\n"
        groups = self.generate_groups(code)
        group0 = list(groups)[0]
        glob = {}
        exec('__name__ = "__main__"', glob)
        res = group0.execute(glob, OutputCage())
        self.assertEqual(res['standard output'], '5\n')

    def test_grouping_if_else(self):
        groups = self.generate_groups(source_test_if_else)
        groups = list(groups)
        self.assertEqual(len(groups), 1)

    def test_grouping_for_else(self):
        groups = self.generate_groups(source_test_for_else)
        groups = list(groups)
        self.assertEqual(len(groups), 1)

    def test_grouping_try_except(self):
        groups = self.generate_groups(source_test_try_except)
        groups = list(groups)
        self.assertEqual(len(groups), 1)

    def test_grouping_decorator(self):
        groups = self.generate_groups(source_grouping_decorator)
        groups = list(groups)
        self.assertEqual(len(groups), 1)

    def test_divide_in_lines(self):
        code = "if __name__ == '__main__':\n\tprint(5)\n"
        groups = self.generate_groups(code)
        code_str = StringIO(code)
        lines_expected = _generate_logical_lines(code_str.readline)
        lines_obtained = sum([group.lines for group in groups], [])
        self.assertEqual(lines_expected, lines_obtained)

    def test_docstring_extraction_with_comments(self):
        origin = StringIO(source_docstring_extraction_with_comments).readline
        groups = list(CodeGroup.iterate_groups_from_source(origin))
        self.assertEqual(len(groups), 1)
        group0 = groups[0]
        obtained = group0.extract_docstrings()[0].strip()
        obtained = _normalize_str(obtained)
        expected = _normalize_str(expected_docstring_extraction_with_comments)
        self.assertEqual(obtained, expected)

    def test_title_in_warnings(self):
        glob = {}
        exec('import sys', glob)
        code = "print('=================', file=sys.stderr)\n"
        group0 = list(self.generate_groups(code))[0]
        group0.execute(glob, OutputCage())
        obtained = group0.compile('.')[0]
        obtained = _normalize_str(obtained)
        expected = _normalize_str(expected_title_in_warnings)
        self.assertEqual(obtained, expected)


# %%
"""
Command Line Execution
=======================
"""

if __name__ == '__main__':
    if len(sys.argv) == 1:
        print('running it with empty arguments runs the tests')
        print('the first argument is the script you want to compile')
        print('other arguments are passed as argv to the script')
        unittest.main()
    else:
        input_file = sys.argv[1]
        input_file = os.path.normpath(input_file)
        base_dir = os.path.dirname(input_file)
        filename = os.path.basename(input_file)
        output_dir = os.path.join(base_dir, 'compiled_{}'.format(filename))
        output_dir = os.path.normpath(output_dir)
        print(input_file, output_dir, sys.argv[2:])
        total_path_in = os.path.join(base_dir, input_file)
        argv = [os.path.abspath(total_path_in)] + sys.argv[2:]
        run_file(os.path.abspath(total_path_in), output_dir, argv)
