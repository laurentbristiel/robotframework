#  Copyright 2008-2015 Nokia Networks
#  Copyright 2016-     Robot Framework Foundation
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

"""Module implementing test execution related model objects.

When tests are executed by Robot Framework, a :class:`TestSuite` structure using
classes defined in this module is created by
:class:`~robot.running.builder.builders.TestSuiteBuilder`
based on data on a file system. In addition to that, external tools can
create executable suite structures programmatically.

Regardless the approach to construct it, a :class:`TestSuite` object is executed
by calling its :meth:`~TestSuite.run` method as shown in the example in
the :mod:`robot.running` package level documentation. When a suite is run,
test, keywords, and other objects it contains can be inspected and modified
by using `pre-run modifiers`__ and `listeners`__.

The :class:`TestSuite` class is exposed via the :mod:`robot.api` package. If other
classes are needed, they can be imported from :mod:`robot.running`.

__ http://robotframework.org/robotframework/latest/RobotFrameworkUserGuide.html#programmatic-modification-of-results
__ http://robotframework.org/robotframework/latest/RobotFrameworkUserGuide.html#listener-interface
"""

import warnings
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence, TYPE_CHECKING, Union

from robot import model
from robot.conf import RobotSettings
from robot.errors import BreakLoop, ContinueLoop, DataError, ReturnFromKeyword, VariableError
from robot.model import BodyItem, create_fixture, DataDict, ModelObject, Tags, TestSuites
from robot.output import LOGGER, Output, pyloggingconf
from robot.result import (Break as BreakResult, Continue as ContinueResult,
                          Error as ErrorResult, Return as ReturnResult, Var as VarResult)
from robot.utils import eq, getshortdoc, normalize, NOT_SET, setter
from robot.variables import VariableResolver

from .arguments import ArgumentSpec, EmbeddedArguments, UserKeywordArgumentParser
from .bodyrunner import ForRunner, IfRunner, KeywordRunner, TryRunner, WhileRunner
from .randomizer import Randomizer
from .statusreporter import StatusReporter
from .userkeywordrunner import UserKeywordRunner, EmbeddedArgumentsRunner

if TYPE_CHECKING:
    from robot.parsing import File
    from .builder import TestDefaults


BodyItemParent = Union['TestSuite', 'TestCase', 'UserKeyword', 'For', 'If', 'IfBranch',
                       'Try', 'TryBranch', 'While', None]


class Body(model.BaseBody['Keyword', 'For', 'While', 'If', 'Try', 'Var', 'Return',
                          'Continue', 'Break', 'model.Message', 'Error']):
    __slots__ = []


class WithSource:
    __slots__ = ()
    parent: BodyItemParent

    @property
    def source(self) -> 'Path|None':
        return self.parent.source if self.parent is not None else None


@Body.register
class Keyword(model.Keyword, WithSource):
    """Represents an executable keyword call.

    A keyword call consists only of a keyword name, arguments and possible
    assignment in the data::

        Keyword    arg
        ${result} =    Another Keyword    arg1    arg2

    The actual keyword that is executed depends on the context where this model
    is executed.
    """
    __slots__ = ['lineno']

    def __init__(self, name: str = '',
                 args: Sequence[str] = (),
                 assign: Sequence[str] = (),
                 type: str = BodyItem.KEYWORD,
                 parent: BodyItemParent = None,
                 lineno: 'int|None' = None):
        super().__init__(name, args, assign, type, parent)
        self.lineno = lineno

    def to_dict(self) -> DataDict:
        data = super().to_dict()
        if self.lineno:
            data['lineno'] = self.lineno
        return data

    def run(self, context, run=True, templated=None):
        return KeywordRunner(context, run).run(self)


@Body.register
class For(model.For, WithSource):
    __slots__ = ['lineno', 'error']
    body_class = Body

    def __init__(self, assign: Sequence[str] = (),
                 flavor: Literal['IN', 'IN RANGE', 'IN ENUMERATE', 'IN ZIP'] = 'IN',
                 values: Sequence[str] = (),
                 start: 'str|None' = None,
                 mode: 'str|None' = None,
                 fill: 'str|None' = None,
                 parent: BodyItemParent = None,
                 lineno: 'int|None' = None,
                 error: 'str|None' = None):
        super().__init__(assign, flavor, values, start, mode, fill, parent)
        self.lineno = lineno
        self.error = error

    @classmethod
    def from_dict(cls, data: DataDict) -> 'For':
        # RF 6.1 compatibility
        if 'variables' in data:
            data['assign'] = data.pop('variables')
        return super().from_dict(data)

    def to_dict(self) -> DataDict:
        data = super().to_dict()
        if self.lineno:
            data['lineno'] = self.lineno
        if self.error:
            data['error'] = self.error
        return data

    def run(self, context, run=True, templated=False):
        return ForRunner(context, self.flavor, run, templated).run(self)


@Body.register
class While(model.While, WithSource):
    __slots__ = ['lineno', 'error']
    body_class = Body

    def __init__(self, condition: 'str|None' = None,
                 limit: 'str|None' = None,
                 on_limit: 'str|None' = None,
                 on_limit_message: 'str|None' = None,
                 parent: BodyItemParent = None,
                 lineno: 'int|None' = None,
                 error: 'str|None' = None):
        super().__init__(condition, limit, on_limit, on_limit_message, parent)
        self.lineno = lineno
        self.error = error

    def to_dict(self) -> DataDict:
        data = super().to_dict()
        if self.lineno:
            data['lineno'] = self.lineno
        if self.error:
            data['error'] = self.error
        return data

    def run(self, context, run=True, templated=False):
        return WhileRunner(context, run, templated).run(self)


class IfBranch(model.IfBranch, WithSource):
    __slots__ = ['lineno']
    body_class = Body

    def __init__(self, type: str = BodyItem.IF,
                 condition: 'str|None' = None,
                 parent: BodyItemParent = None,
                 lineno: 'int|None' = None):
        super().__init__(type, condition, parent)
        self.lineno = lineno

    def to_dict(self) -> DataDict:
        data = super().to_dict()
        if self.lineno:
            data['lineno'] = self.lineno
        return data


@Body.register
class If(model.If, WithSource):
    __slots__ = ['lineno', 'error']
    branch_class = IfBranch

    def __init__(self, parent: BodyItemParent = None,
                 lineno: 'int|None' = None,
                 error: 'str|None' = None):
        super().__init__(parent)
        self.lineno = lineno
        self.error = error

    def run(self, context, run=True, templated=False):
        return IfRunner(context, run, templated).run(self)

    def to_dict(self) -> DataDict:
        data = super().to_dict()
        if self.lineno:
            data['lineno'] = self.lineno
        if self.error:
            data['error'] = self.error
        return data


class TryBranch(model.TryBranch, WithSource):
    __slots__ = ['lineno']
    body_class = Body

    def __init__(self, type: str = BodyItem.TRY,
                 patterns: Sequence[str] = (),
                 pattern_type: 'str|None' = None,
                 assign: 'str|None' = None,
                 parent: BodyItemParent = None,
                 lineno: 'int|None' = None):
        super().__init__(type, patterns, pattern_type, assign, parent)
        self.lineno = lineno

    @classmethod
    def from_dict(cls, data: DataDict) -> 'TryBranch':
        # RF 6.1 compatibility.
        if 'variable' in data:
            data['assign'] = data.pop('variable')
        return super().from_dict(data)

    def to_dict(self) -> DataDict:
        data = super().to_dict()
        if self.lineno:
            data['lineno'] = self.lineno
        return data


@Body.register
class Try(model.Try, WithSource):
    __slots__ = ['lineno', 'error']
    branch_class = TryBranch

    def __init__(self, parent: BodyItemParent = None,
                 lineno: 'int|None' = None,
                 error: 'str|None' = None):
        super().__init__(parent)
        self.lineno = lineno
        self.error = error

    def run(self, context, run=True, templated=False):
        return TryRunner(context, run, templated).run(self)

    def to_dict(self) -> DataDict:
        data = super().to_dict()
        if self.lineno:
            data['lineno'] = self.lineno
        if self.error:
            data['error'] = self.error
        return data


@Body.register
class Var(model.Var, WithSource):
    __slots__ = ['lineno', 'error']

    def __init__(self, name: str = '',
                 value: 'str|Sequence[str]' = (),
                 scope: 'str|None' = None,
                 separator: 'str|None' = None,
                 parent: BodyItemParent = None,
                 lineno: 'int|None' = None,
                 error: 'str|None' = None):
        super().__init__(name, value, scope, separator, parent)
        self.lineno = lineno
        self.error = error

    def run(self, context, run=True, templated=False):
        result = VarResult(self.name, self.value, self.scope, self.separator)
        with StatusReporter(self, result, context, run):
            if run:
                if self.error:
                    raise DataError(self.error, syntax=True)
                if not context.dry_run:
                    scope = self._get_scope(context.variables)
                    setter = getattr(context.variables, f'set_{scope}')
                    try:
                        resolver = VariableResolver.from_variable(self)
                        setter(self.name, resolver.resolve(context.variables))
                    except DataError as err:
                        raise VariableError(f"Setting variable '{self.name}' failed: {err}")

    def _get_scope(self, variables):
        if not self.scope:
            return 'local'
        try:
            scope = variables.replace_string(self.scope)
            if scope.upper() == 'TASK':
                return 'test'
            if scope.upper() in ('GLOBAL', 'SUITE', 'TEST', 'LOCAL'):
                return scope.lower()
            raise DataError(f"Value '{scope}' is not accepted. Valid values are "
                            f"'GLOBAL', 'SUITE', 'TEST', 'TASK' and 'LOCAL'.")
        except DataError as err:
            raise DataError(f"Invalid VAR scope: {err}")

    def to_dict(self) -> DataDict:
        data = super().to_dict()
        if self.lineno:
            data['lineno'] = self.lineno
        if self.error:
            data['error'] = self.error
        return data


@Body.register
class Return(model.Return, WithSource):
    __slots__ = ['lineno', 'error']

    def __init__(self, values: Sequence[str] = (),
                 parent: BodyItemParent = None,
                 lineno: 'int|None' = None,
                 error: 'str|None' = None):
        super().__init__(values, parent)
        self.lineno = lineno
        self.error = error

    def run(self, context, run=True, templated=False):
        with StatusReporter(self, ReturnResult(self.values), context, run):
            if run:
                if self.error:
                    raise DataError(self.error, syntax=True)
                if not context.dry_run:
                    raise ReturnFromKeyword(self.values)

    def to_dict(self) -> DataDict:
        data = super().to_dict()
        if self.lineno:
            data['lineno'] = self.lineno
        if self.error:
            data['error'] = self.error
        return data


@Body.register
class Continue(model.Continue, WithSource):
    __slots__ = ['lineno', 'error']

    def __init__(self, parent: BodyItemParent = None,
                 lineno: 'int|None' = None,
                 error: 'str|None' = None):
        super().__init__(parent)
        self.lineno = lineno
        self.error = error

    def run(self, context, run=True, templated=False):
        with StatusReporter(self, ContinueResult(), context, run):
            if run:
                if self.error:
                    raise DataError(self.error, syntax=True)
                if not context.dry_run:
                    raise ContinueLoop()

    def to_dict(self) -> DataDict:
        data = super().to_dict()
        if self.lineno:
            data['lineno'] = self.lineno
        if self.error:
            data['error'] = self.error
        return data


@Body.register
class Break(model.Break, WithSource):
    __slots__ = ['lineno', 'error']

    def __init__(self, parent: BodyItemParent = None,
                 lineno: 'int|None' = None,
                 error: 'str|None' = None):
        super().__init__(parent)
        self.lineno = lineno
        self.error = error

    def run(self, context, run=True, templated=False):
        with StatusReporter(self, BreakResult(), context, run):
            if run:
                if self.error:
                    raise DataError(self.error, syntax=True)
                if not context.dry_run:
                    raise BreakLoop()

    def to_dict(self) -> DataDict:
        data = super().to_dict()
        if self.lineno:
            data['lineno'] = self.lineno
        if self.error:
            data['error'] = self.error
        return data


@Body.register
class Error(model.Error, WithSource):
    __slots__ = ['lineno', 'error']

    def __init__(self, values: Sequence[str] = (),
                 parent: BodyItemParent = None,
                 lineno: 'int|None' = None,
                 error: str = ''):
        super().__init__(values, parent)
        self.lineno = lineno
        self.error = error

    def run(self, context, run=True, templated=False):
        with StatusReporter(self, ErrorResult(self.values), context, run):
            if run:
                raise DataError(self.error)

    def to_dict(self) -> DataDict:
        data = super().to_dict()
        if self.lineno:
            data['lineno'] = self.lineno
        data['error'] = self.error
        return data


class TestCase(model.TestCase[Keyword]):
    """Represents a single executable test case.

    See the base class for documentation of attributes not documented here.
    """
    __slots__ = ['template', 'error']
    body_class = Body        #: Internal usage only.
    fixture_class = Keyword  #: Internal usage only.

    def __init__(self, name: str = '',
                 doc: str = '',
                 tags: Sequence[str] = (),
                 timeout: 'str|None' = None,
                 lineno: 'int|None' = None,
                 parent: 'TestSuite|None' = None,
                 template: 'str|None' = None,
                 error: 'str|None' = None):
        super().__init__(name, doc, tags, timeout, lineno, parent)
        #: Name of the keyword that has been used as a template when building the test.
        # ``None`` if template is not used.
        self.template = template
        self.error = error

    def to_dict(self) -> DataDict:
        data = super().to_dict()
        if self.template:
            data['template'] = self.template
        if self.error:
            data['error'] = self.error
        return data

    @setter
    def body(self, body: 'Sequence[BodyItem|DataDict]') -> Body:
        """Test body as a :class:`~robot.running.Body` object."""
        return self.body_class(self, body)


class TestSuite(model.TestSuite[Keyword, TestCase]):
    """Represents a single executable test suite.

    See the base class for documentation of attributes not documented here.
    """
    __slots__ = []
    test_class = TestCase    #: Internal usage only.
    fixture_class = Keyword  #: Internal usage only.

    def __init__(self, name: str = '',
                 doc: str = '',
                 metadata: 'Mapping[str, str]|None' = None,
                 source: 'Path|str|None' = None,
                 rpa: 'bool|None' = False,
                 parent: 'TestSuite|None' = None):
        super().__init__(name, doc, metadata, source, rpa, parent)
        #: :class:`ResourceFile` instance containing imports, variables and
        #: keywords the suite owns. When data is parsed from the file system,
        #: this data comes from the same test case file that creates the suite.
        self.resource = ResourceFile(owner=self)

    @setter
    def resource(self, resource: 'ResourceFile|dict') -> 'ResourceFile':
        if isinstance(resource, dict):
            resource = ResourceFile.from_dict(resource)
            resource.owner = self
        return resource

    @classmethod
    def from_file_system(cls, *paths: 'Path|str', **config) -> 'TestSuite':
        """Create a :class:`TestSuite` object based on the given ``paths``.

        :param paths: File or directory paths where to read the data from.
        :param config: Configuration parameters for :class:`~.builders.TestSuiteBuilder`
            class that is used internally for building the suite.

        See also :meth:`from_model` and :meth:`from_string`.
        """
        from .builder import TestSuiteBuilder
        return TestSuiteBuilder(**config).build(*paths)

    @classmethod
    def from_model(cls, model: 'File', name: 'str|None' = None, *,
                   defaults: 'TestDefaults|None' = None) -> 'TestSuite':
        """Create a :class:`TestSuite` object based on the given ``model``.

        :param model: Model to create the suite from.
        :param name: Deprecated since Robot Framework 6.1.
        :param defaults: Possible test specific defaults from suite
            initialization files. New in Robot Framework 6.1.

        The model can be created by using the
        :func:`~robot.parsing.parser.parser.get_model` function and possibly
        modified by other tooling in the :mod:`robot.parsing` module.

        Giving suite name is deprecated and users should set it and possible
        other attributes to the returned suite separately. One easy way is using
        the :meth:`config` method like this::

            suite = TestSuite.from_model(model).config(name='X', doc='Example')

        See also :meth:`from_file_system` and :meth:`from_string`.
        """
        from .builder import RobotParser
        suite = RobotParser().parse_model(model, defaults)
        if name is not None:
            # TODO: Remove 'name' in RF 7.
            warnings.warn("'name' argument of 'TestSuite.from_model' is deprecated. "
                          "Set the name to the returned suite separately.")
            suite.name = name
        return suite

    @classmethod
    def from_string(cls, string: str, *, defaults: 'TestDefaults|None' = None,
                    **config) -> 'TestSuite':
        """Create a :class:`TestSuite` object based on the given ``string``.

        :param string: String to create the suite from.
        :param defaults: Possible test specific defaults from suite
            initialization files.
        :param config: Configuration parameters for
             :func:`~robot.parsing.parser.parser.get_model` used internally.

        If suite name or other attributes need to be set, an easy way is using
        the :meth:`config` method like this::

            suite = TestSuite.from_string(string).config(name='X', doc='Example')

        New in Robot Framework 6.1. See also :meth:`from_model` and
        :meth:`from_file_system`.
        """
        from robot.parsing import get_model
        model = get_model(string, data_only=True, **config)
        return cls.from_model(model, defaults=defaults)

    def configure(self, randomize_suites: bool = False, randomize_tests: bool = False,
                  randomize_seed: 'int|None' = None, **options):
        """A shortcut to configure a suite using one method call.

        Can only be used with the root test suite.

        :param randomize_xxx: Passed to :meth:`randomize`.
        :param options: Passed to
            :class:`~robot.model.configurer.SuiteConfigurer` that will then
            set suite attributes, call :meth:`filter`, etc. as needed.

        Example::

            suite.configure(included_tags=['smoke'],
                            doc='Smoke test results.')

        Not to be confused with :meth:`config` method that suites, tests,
        and keywords have to make it possible to set multiple attributes in
        one call.
        """
        super().configure(**options)
        self.randomize(randomize_suites, randomize_tests, randomize_seed)

    def randomize(self, suites: bool = True, tests: bool = True,
                  seed: 'int|None' = None):
        """Randomizes the order of suites and/or tests, recursively.

        :param suites: Boolean controlling should suites be randomized.
        :param tests: Boolean controlling should tests be randomized.
        :param seed: Random seed. Can be given if previous random order needs
            to be re-created. Seed value is always shown in logs and reports.
        """
        self.visit(Randomizer(suites, tests, seed))

    @setter
    def suites(self, suites: 'Sequence[TestSuite|DataDict]') -> TestSuites['TestSuite']:
        return TestSuites['TestSuite'](self.__class__, self, suites)

    def run(self, settings=None, **options):
        """Executes the suite based on the given ``settings`` or ``options``.

        :param settings: :class:`~robot.conf.settings.RobotSettings` object
            to configure test execution.
        :param options: Used to construct new
            :class:`~robot.conf.settings.RobotSettings` object if ``settings``
            are not given.
        :return: :class:`~robot.result.executionresult.Result` object with
            information about executed suites and tests.

        If ``options`` are used, their names are the same as long command line
        options except without hyphens. Some options are ignored (see below),
        but otherwise they have the same semantics as on the command line.
        Options that can be given on the command line multiple times can be
        passed as lists like ``variable=['VAR1:value1', 'VAR2:value2']``.
        If such an option is used only once, it can be given also as a single
        string like ``variable='VAR:value'``.

        Additionally, listener option allows passing object directly instead of
        listener name, e.g. ``run('tests.robot', listener=Listener())``.

        To capture stdout and/or stderr streams, pass open file objects in as
        special keyword arguments ``stdout`` and ``stderr``, respectively.

        Only options related to the actual test execution have an effect.
        For example, options related to selecting or modifying test cases or
        suites (e.g. ``--include``, ``--name``, ``--prerunmodifier``) or
        creating logs and reports are silently ignored. The output XML
        generated as part of the execution can be configured, though. This
        includes disabling it with ``output=None``.

        Example::

            stdout = StringIO()
            result = suite.run(variable='EXAMPLE:value',
                               output='example.xml',
                               exitonfailure=True,
                               stdout=stdout)
            print(result.return_code)

        To save memory, the returned
        :class:`~robot.result.executionresult.Result` object does not
        have any information about the executed keywords. If that information
        is needed, the created output XML file needs to be read  using the
        :class:`~robot.result.resultbuilder.ExecutionResult` factory method.

        See the :mod:`package level <robot.running>` documentation for
        more examples, including how to construct executable test suites and
        how to create logs and reports based on the execution results.

        See the :func:`robot.run <robot.run.run>` function for a higher-level
        API for executing tests in files or directories.
        """
        from .namespace import IMPORTER
        from .signalhandler import STOP_SIGNAL_MONITOR
        from .suiterunner import SuiteRunner

        with LOGGER:
            if not settings:
                settings = RobotSettings(options)
                LOGGER.register_console_logger(**settings.console_output_config)
            with pyloggingconf.robot_handler_enabled(settings.log_level):
                with STOP_SIGNAL_MONITOR:
                    IMPORTER.reset()
                    output = Output(settings)
                    runner = SuiteRunner(output, settings)
                    self.visit(runner)
                output.close(runner.result)
        return runner.result

    def to_dict(self) -> DataDict:
        data = super().to_dict()
        data['resource'] = self.resource.to_dict()
        return data


class Variable(ModelObject):
    repr_args = ('name', 'value', 'separator')

    def __init__(self, name: str = '',
                 value: Sequence[str] = (),
                 separator: 'str|None' = None,
                 owner: 'ResourceFile|None' = None,
                 lineno: 'int|None' = None,
                 error: 'str|None' = None):
        self.name = name
        self.value = tuple(value)
        self.separator = separator
        self.owner = owner
        self.lineno = lineno
        self.error = error

    @property
    def source(self) -> 'Path|None':
        return self.owner.source if self.owner is not None else None

    def report_error(self, message: str, level: str = 'ERROR'):
        source = self.source or '<unknown>'
        line = f' on line {self.lineno}' if self.lineno else ''
        LOGGER.write(f"Error in file '{source}'{line}: "
                     f"Setting variable '{self.name}' failed: {message}", level)

    def to_dict(self) -> DataDict:
        data = {'name': self.name, 'value': self.value}
        if self.lineno:
            data['lineno'] = self.lineno
        if self.error:
            data['error'] = self.error
        return data


class ResourceFile(ModelObject):
    repr_args = ('source',)
    __slots__ = ('_source', 'owner', 'doc')

    def __init__(self, source: 'Path|str|None' = None,
                 owner: 'TestSuite|None' = None,
                 doc: str = ''):
        self.source = source
        self.owner = owner    # FIXME: Should this be 'parent' instead?
        self.doc = doc
        self.imports = []
        self.variables = []
        self.keywords = []

    @property
    def source(self) -> 'Path|None':
        if self._source:
            return self._source
        if self.owner:
            return self.owner.source
        return None

    @source.setter
    def source(self, source: 'Path|str|None'):
        if isinstance(source, str):
            source = Path(source)
        self._source = source

    @property
    def name(self) -> 'str|None':
        """Resource file name.

        ``None`` if resource file is part of a suite or if it does not have
        :attr:`source`, name of the source file without the extension otherwise.
        """
        if self.owner or not self.source:
            return None
        return self.source.stem

    @setter
    def imports(self, imports: Sequence['Import']) -> 'Imports':
        return Imports(self, imports)

    @setter
    def variables(self, variables: Sequence['Variable']) -> 'Variables':
        return Variables(self, variables)

    @setter
    def keywords(self, keywords: Sequence['UserKeyword']) -> 'UserKeywords':
        return UserKeywords(self, keywords)

    @classmethod
    def from_file_system(cls, path: 'Path|str', **config) -> 'ResourceFile':
        """Create a :class:`ResourceFile` object based on the give ``path``.

        :param path: File path where to read the data from.
        :param config: Configuration parameters for :class:`~.builders.ResourceFileBuilder`
            class that is used internally for building the suite.

        New in Robot Framework 6.1. See also :meth:`from_string` and :meth:`from_model`.
        """
        from .builder import ResourceFileBuilder
        return ResourceFileBuilder(**config).build(path)

    @classmethod
    def from_string(cls, string: str, **config) -> 'ResourceFile':
        """Create a :class:`ResourceFile` object based on the given ``string``.

        :param string: String to create the resource file from.
        :param config: Configuration parameters for
             :func:`~robot.parsing.parser.parser.get_resource_model` used internally.

        New in Robot Framework 6.1. See also :meth:`from_file_system` and
        :meth:`from_model`.
        """
        from robot.parsing import get_resource_model
        model = get_resource_model(string, data_only=True, **config)
        return cls.from_model(model)

    @classmethod
    def from_model(cls, model: 'File') -> 'ResourceFile':
        """Create a :class:`ResourceFile` object based on the given ``model``.

        :param model: Model to create the suite from.

        The model can be created by using the
        :func:`~robot.parsing.parser.parser.get_resource_model` function and possibly
        modified by other tooling in the :mod:`robot.parsing` module.

        New in Robot Framework 6.1. See also :meth:`from_file_system` and
        :meth:`from_string`.
        """
        from .builder import RobotParser
        return RobotParser().parse_resource_model(model)

    def find_keywords(self, name: str,
                      include_embedded: bool = True) -> 'list[UserKeyword]':
        keywords = []
        norm_name = normalize(name, ignore='_')
        for kw in self.keywords:
            if kw.embedded:
                if include_embedded and kw.matches(name):
                    keywords.append(kw)
            else:
                if normalize(kw.name, ignore='_') == norm_name:
                    keywords.append(kw)
        return keywords

    def to_dict(self) -> DataDict:
        data = {}
        if self._source:
            data['source'] = str(self._source)
        if self.doc:
            data['doc'] = self.doc
        if self.imports:
            data['imports'] = self.imports.to_dicts()
        if self.variables:
            data['variables'] = self.variables.to_dicts()
        if self.keywords:
            data['keywords'] = self.keywords.to_dicts()
        return data


class UserKeyword(ModelObject):
    repr_args = ('name', 'args')
    fixture_class = Keyword
    __slots__ = ['embedded', 'doc', 'timeout', 'lineno', 'owner', 'error',
                 '_setup', '_teardown']

    def __init__(self, name: str = '',
                 args: 'ArgumentSpec|Sequence[str]' = (),
                 doc: str = '',
                 tags: 'Tags|Sequence[str]' = (),
                 timeout: 'str|None' = None,
                 lineno: 'int|None' = None,
                 owner: 'ResourceFile|None' = None,
                 error: 'str|None' = None):
        self.embedded: EmbeddedArguments | None = None
        self.name = name
        self.args = args
        self.doc = doc
        self.tags = tags
        self.timeout = timeout
        self.lineno = lineno
        self.owner = owner
        self.error = error
        self.body = []
        self._setup = None
        self._teardown = None

    # FIXME: Decide between `args`, `arguments` and `parameters`.
    @property
    def arguments(self):
        return self.args

    @setter
    def name(self, name: str) -> str:
        self.embedded = EmbeddedArguments.from_name(name)
        return name

    @property
    def full_name(self):
        if self.owner and self.owner.name:
            return f'{self.owner.name}.{self.name}'
        return self.name

    @setter
    def args(self, spec: 'ArgumentSpec|Sequence[str]') -> ArgumentSpec:
        if not isinstance(spec, ArgumentSpec):
            spec = UserKeywordArgumentParser().parse(spec)
        spec.name = lambda: self.full_name
        return spec

    @property
    def short_doc(self):
        return getshortdoc(self.doc)

    @setter
    def tags(self, tags: 'Tags|Sequence[str]') -> Tags:
        return Tags(tags)

    @property
    def private(self):
        return bool(self.tags and self.tags.robot('private'))

    @property
    def source(self) -> 'Path|None':
        return self.owner.source if self.owner is not None else None

    @setter
    def body(self, body: 'Sequence[BodyItem|DataDict]') -> Body:
        return Body(self, body)

    @property
    def setup(self) -> Keyword:
        """User keyword setup as a :class:`Keyword` object.

        New in Robot Framework 7.0.
        """
        if self._setup is None:
            self.setup = None
        return self._setup

    @setup.setter
    def setup(self, setup: 'Keyword|DataDict|None'):
        self._setup = create_fixture(self.fixture_class, setup, self, Keyword.SETUP)

    @property
    def has_setup(self) -> bool:
        """Check does a keyword have a setup without creating a setup object.

        See :attr:`has_teardown` for more information. New in Robot Framework 7.0.
        """
        return bool(self._setup)

    @property
    def teardown(self) -> Keyword:
        """User keyword teardown as a :class:`Keyword` object."""
        if self._teardown is None:
            self.teardown = None
        return self._teardown

    @teardown.setter
    def teardown(self, teardown: 'Keyword|DataDict|None'):
        self._teardown = create_fixture(self.fixture_class, teardown, self, Keyword.TEARDOWN)

    @property
    def has_teardown(self) -> bool:
        """Check does a keyword have a teardown without creating a teardown object.

        A difference between using ``if kw.has_teardown:`` and ``if kw.teardown:``
        is that accessing the :attr:`teardown` attribute creates a :class:`Keyword`
        object representing the teardown even when the user keyword actually does
        not have one. This can have an effect on memory usage.

        New in Robot Framework 6.1.
        """
        return bool(self._teardown)

    def matches(self, name: str) -> bool:
        if self.embedded:
            return self.embedded.match(name)
        return eq(self.name, name, ignore='_')

    def create_runner(self, name, languages=None):
        if self.embedded:
            return EmbeddedArgumentsRunner(self, name)
        return UserKeywordRunner(self)

    def to_dict(self) -> DataDict:
        data: DataDict = {'name': self.name}
        for name, value in [('args', tuple(self._decorate_args())),
                            ('doc', self.doc),
                            ('tags', tuple(self.tags)),
                            ('timeout', self.timeout),
                            ('lineno', self.lineno),
                            ('error', self.error)]:
            if value:
                data[name] = value
        if self.has_setup:
            data['setup'] = self.setup.to_dict()
        data['body'] = self.body.to_dicts()
        if self.has_teardown:
            data['teardown'] = self.teardown.to_dict()
        return data

    def _decorate_args(self):
        args = []
        for info in self.args:
            if info.kind == info.VAR_NAMED:
                deco = '&'
            elif info.kind in (info.VAR_POSITIONAL, info.NAMED_ONLY_MARKER):
                deco = '@'
            else:
                deco = '$'
            arg = f'{deco}{{{info.name}}}'
            if info.default is not NOT_SET:
                arg = f'{arg}={info.default}'
            args.append(arg)
        return args

    def _include_in_repr(self, name: str, value: Any) -> bool:
        return name == 'name' or value

    def _repr_format(self, name: str, value: Any) -> str:
        if name == 'args':
            return repr(self._decorate_args())
        return super()._repr_format(name, value)


class Import(ModelObject):
    repr_args = ('type', 'name', 'args', 'alias')
    LIBRARY = 'LIBRARY'
    RESOURCE = 'RESOURCE'
    VARIABLES = 'VARIABLES'

    def __init__(self, type: Literal['LIBRARY', 'RESOURCE', 'VARIABLES'],
                 name: str,
                 args: Sequence[str] = (),
                 alias: 'str|None' = None,
                 owner: 'ResourceFile|None' = None,
                 lineno: 'int|None' = None):
        if type not in (self.LIBRARY, self.RESOURCE, self.VARIABLES):
            raise ValueError(f"Invalid import type: Expected '{self.LIBRARY}', "
                             f"'{self.RESOURCE}' or '{self.VARIABLES}', got '{type}'.")
        self.type = type
        self.name = name
        self.args = tuple(args)
        self.alias = alias
        self.owner = owner
        self.lineno = lineno

    @property
    def source(self) -> 'Path|None':
        return self.owner.source if self.owner is not None else None

    @property
    def directory(self) -> 'Path|None':
        source = self.source
        return source.parent if source and not source.is_dir() else source

    @property
    def setting_name(self) -> str:
        return self.type.title()

    def select(self, library: Any, resource: Any, variables: Any) -> Any:
        return {self.LIBRARY: library,
                self.RESOURCE: resource,
                self.VARIABLES: variables}[self.type]

    def report_error(self, message: str, level: str = 'ERROR'):
        source = self.source or '<unknown>'
        line = f' on line {self.lineno}' if self.lineno else ''
        LOGGER.write(f"Error in file '{source}'{line}: {message}", level)

    @classmethod
    def from_dict(cls, data) -> 'Import':
        return cls(**data)

    def to_dict(self) -> DataDict:
        data: DataDict = {'type': self.type, 'name': self.name}
        if self.args:
            data['args'] = self.args
        if self.alias:
            data['alias'] = self.alias
        if self.lineno:
            data['lineno'] = self.lineno
        return data

    def _include_in_repr(self, name: str, value: Any) -> bool:
        return name in ('type', 'name') or value


class Imports(model.ItemList):

    def __init__(self, owner: ResourceFile, imports: Sequence[Import] = ()):
        super().__init__(Import, {'owner': owner}, items=imports)

    def library(self, name: str, args: Sequence[str] = (), alias: 'str|None' = None,
                lineno: 'int|None' = None) -> Import:
        """Create library import."""
        return self.create(Import.LIBRARY, name, args, alias, lineno=lineno)

    def resource(self, name: str, lineno: 'int|None' = None) -> Import:
        """Create resource import."""
        return self.create(Import.RESOURCE, name, lineno=lineno)

    def variables(self, name: str, args: Sequence[str] = (),
                  lineno: 'int|None' = None) -> Import:
        """Create variables import."""
        return self.create(Import.VARIABLES, name, args, lineno=lineno)

    def create(self, *args, **kwargs) -> Import:
        """Generic method for creating imports.

        Import type specific methods :meth:`library`, :meth:`resource` and
        :meth:`variables` are recommended over this method.
        """
        # RF 6.1 changed types to upper case. Code below adds backwards compatibility.
        if args:
            args = (args[0].upper(),) + args[1:]
        elif 'type' in kwargs:
            kwargs['type'] = kwargs['type'].upper()
        return super().create(*args, **kwargs)


class Variables(model.ItemList[Variable]):

    def __init__(self, owner: ResourceFile, variables: Sequence[Variable] = ()):
        super().__init__(Variable, {'owner': owner}, items=variables)


class UserKeywords(model.ItemList[UserKeyword]):

    def __init__(self, owner: ResourceFile, keywords: Sequence[UserKeyword] = ()):
        super().__init__(UserKeyword, {'owner': owner}, items=keywords)
