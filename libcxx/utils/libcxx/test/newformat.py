#===----------------------------------------------------------------------===##
#
# Part of the LLVM Project, under the Apache License v2.0 with LLVM Exceptions.
# See https://llvm.org/LICENSE.txt for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
#===----------------------------------------------------------------------===##

import lit
import os
import pipes

class CxxStandardLibraryTest(lit.formats.TestFormat):
    """
    Lit test format for the C++ Standard Library conformance test suite.

    This test format is based on top of the ShTest format -- it basically
    creates a shell script performing the right operations (compile/link/run)
    based on the extension of the test file it encounters. It supports files
    with the following extensions:

    FOO.pass.cpp            - Compiles, links and runs successfully
    FOO.pass.mm             - Same as .pass.cpp, but for Objective-C++
    FOO.run.fail.cpp        - Compiles and links successfully, but fails at runtime

    FOO.compile.pass.cpp    - Compiles successfully, link and run not attempted
    FOO.compile.fail.cpp    - Does not compile successfully

    FOO.link.pass.cpp       - Compiles and links successfully, run not attempted
    FOO.link.fail.cpp       - Compiles successfully, but fails to link

    FOO.sh.cpp              - A builtin lit Shell test
    FOO.sh.s                - A builtin lit Shell test

    FOO.verify.cpp          - Compiles with clang-verify

    FOO.fail.cpp            - Compiled with clang-verify if clang-verify is
                              supported, and equivalent to a .compile.fail.cpp
                              test otherwise. This is supported only for backwards
                              compatibility with the test suite.

    The test format operates by assuming that each test's configuration provides
    the following substitutions, which it will reuse in the shell scripts it
    constructs:
        %{cxx}           - A command that can be used to invoke the compiler
        %{compile_flags} - Flags to use when compiling a test case
        %{link_flags}    - Flags to use when linking a test case
        %{flags}         - Flags to use either when compiling or linking a test case
        %{exec}          - A command to prefix the execution of executables

    Note that when building an executable (as opposed to only compiling a source
    file), all three of ${flags}, %{compile_flags} and %{link_flags} will be used
    in the same command line. In other words, the test format doesn't perform
    separate compilation and linking steps in this case.


    In addition to everything that's supported in Lit ShTests, this test format
    also understands the following directives inside test files:

        // FILE_DEPENDENCIES: file, directory, /path/to/file

            This directive expresses that the test requires the provided files
            or directories in order to run. An example is a test that requires
            some test input stored in a data file. When a test file contains
            such a directive, this test format will collect them and make them
            available in a special %{file_dependencies} substitution. The intent
            is that if one needs to e.g. execute tests on a remote host, the
            %{exec} substitution could use %{file_dependencies} to know which
            files and directories to copy to the remote host.

        // ADDITIONAL_COMPILE_FLAGS: flag1, flag2, flag3

            This directive will cause the provided flags to be added to the
            %{compile_flags} substitution for the test that contains it. This
            allows adding special compilation flags without having to use a
            .sh.cpp test, which would be more powerful but perhaps overkill.


    Design note:
    This test format never implicitly disables a type of test. For example,
    we could be tempted to automatically mark `.verify.cpp` tests as
    UNSUPPORTED when clang-verify isn't supported by the compiler. However,
    this sort of logic has been known to cause tests to be ignored in the
    past, so we favour having tests mark themselves as unsupported explicitly.

    This test format still needs work in the following areas:
        - It is unknown how well it works on Windows yet.
    """
    def getTestsInDirectory(self, testSuite, pathInSuite, litConfig, localConfig):
        SUPPORTED_SUFFIXES = ['.pass.cpp', '.pass.mm', '.run.fail.cpp',
                              '.compile.pass.cpp', '.compile.fail.cpp',
                              '.link.pass.cpp', '.link.fail.cpp',
                              '.sh.cpp', '.sh.s',
                              '.verify.cpp',
                              '.fail.cpp']
        sourcePath = testSuite.getSourcePath(pathInSuite)
        for filename in os.listdir(sourcePath):
            # Ignore dot files and excluded tests.
            if filename.startswith('.') or filename in localConfig.excludes:
                continue

            filepath = os.path.join(sourcePath, filename)
            if not os.path.isdir(filepath):
                if any([filename.endswith(ext) for ext in SUPPORTED_SUFFIXES]):
                    yield lit.Test.Test(testSuite, pathInSuite + (filename,), localConfig)

    def _checkSubstitutions(self, substitutions):
        substitutions = [s for (s, _) in substitutions]
        for s in ['%{cxx}', '%{compile_flags}', '%{link_flags}', '%{flags}', '%{exec}']:
            assert s in substitutions, "Required substitution {} was not provided".format(s)

    # Determine whether clang-verify is supported.
    def _supportsVerify(self, test, litConfig):
        command = "echo | %{cxx} -xc++ - -Werror -fsyntax-only -Xclang -verify-ignore-unexpected"
        result = lit.TestRunner.executeShTest(test, litConfig,
                                              useExternalSh=True,
                                              preamble_commands=[command])
        compilerSupportsVerify = result.code != lit.Test.FAIL
        return compilerSupportsVerify

    def _disableWithModules(self, test, litConfig):
        with open(test.getSourcePath(), 'rb') as f:
            contents = f.read()
        return b'#define _LIBCPP_ASSERT' in contents

    def execute(self, test, litConfig):
        self._checkSubstitutions(test.config.substitutions)
        VERIFY_FLAGS = '-Xclang -verify -Xclang -verify-ignore-unexpected=note -ferror-limit=0'
        filename = test.path_in_suite[-1]

        # TODO(ldionne): We currently disable tests that re-define _LIBCPP_ASSERT
        #                when we run with modules enabled. Instead, we should
        #                split the part that does a death test outside of the
        #                test, and only disable that part when modules are
        #                enabled.
        if '-fmodules' in test.config.available_features and self._disableWithModules(test, litConfig):
            return lit.Test.Result(lit.Test.UNSUPPORTED, 'Test {} is unsupported when modules are enabled')

        if filename.endswith('.sh.cpp') or filename.endswith('.sh.s'):
            steps = [ ] # The steps are already in the script
            return self._executeShTest(test, litConfig, steps)
        elif filename.endswith('.compile.pass.cpp'):
            steps = [
                "%dbg(COMPILED WITH) %{cxx} %s %{flags} %{compile_flags} -fsyntax-only"
            ]
            return self._executeShTest(test, litConfig, steps)
        elif filename.endswith('.compile.fail.cpp'):
            steps = [
                "%dbg(COMPILED WITH) ! %{cxx} %s %{flags} %{compile_flags} -fsyntax-only"
            ]
            return self._executeShTest(test, litConfig, steps)
        elif filename.endswith('.link.pass.cpp'):
            steps = [
                "%dbg(COMPILED WITH) %{cxx} %s %{flags} %{compile_flags} %{link_flags} -o %t.exe"
            ]
            return self._executeShTest(test, litConfig, steps)
        elif filename.endswith('.link.fail.cpp'):
            steps = [
                "%dbg(COMPILED WITH) %{cxx} %s %{flags} %{compile_flags} -c -o %t.o",
                "%dbg(LINKED WITH) ! %{cxx} %t.o %{flags} %{link_flags} -o %t.exe"
            ]
            return self._executeShTest(test, litConfig, steps)
        elif filename.endswith('.run.fail.cpp'):
            steps = [
                "%dbg(COMPILED WITH) %{cxx} %s %{flags} %{compile_flags} %{link_flags} -o %t.exe",
                "%dbg(EXECUTED AS) %{exec} ! %t.exe"
            ]
            return self._executeShTest(test, litConfig, steps, fileDependencies=['%t.exe'])
        elif filename.endswith('.verify.cpp'):
            steps = [
                "%dbg(COMPILED WITH) %{cxx} %s %{flags} %{compile_flags} -fsyntax-only " + VERIFY_FLAGS
            ]
            return self._executeShTest(test, litConfig, steps)
        # Make sure to check these ones last, since they will match other
        # suffixes above too.
        elif filename.endswith('.pass.cpp') or filename.endswith('.pass.mm'):
            steps = [
                "%dbg(COMPILED WITH) %{cxx} %s %{flags} %{compile_flags} %{link_flags} -o %t.exe",
                "%dbg(EXECUTED AS) %{exec} %t.exe"
            ]
            return self._executeShTest(test, litConfig, steps, fileDependencies=['%t.exe'])
        # This is like a .verify.cpp test when clang-verify is supported,
        # otherwise it's like a .compile.fail.cpp test. This is only provided
        # for backwards compatibility with the test suite.
        elif filename.endswith('.fail.cpp'):
            if self._supportsVerify(test, litConfig):
                steps = [
                    "%dbg(COMPILED WITH) %{cxx} %s %{flags} %{compile_flags} -fsyntax-only " + VERIFY_FLAGS
                ]
            else:
                steps = [
                    "%dbg(COMPILED WITH) ! %{cxx} %s %{flags} %{compile_flags} -fsyntax-only"
                ]
            return self._executeShTest(test, litConfig, steps)
        else:
            return lit.Test.Result(lit.Test.UNRESOLVED, "Unknown test suffix for '{}'".format(filename))

    # Utility function to add compile flags in lit.local.cfg files.
    def addCompileFlags(self, config, *flags):
        string = ' '.join(flags)
        config.substitutions = [(s, x + ' ' + string) if s == '%{compile_flags}' else (s, x) for (s, x) in config.substitutions]

    # Modified version of lit.TestRunner.executeShTest to handle custom parsers correctly.
    def _executeShTest(self, test, litConfig, steps, fileDependencies=None):
        if test.config.unsupported:
            return lit.Test.Result(lit.Test.UNSUPPORTED, 'Test is unsupported')

        additionalCompileFlags = []
        fileDependencies = fileDependencies or []
        parsers = [
            lit.TestRunner.IntegratedTestKeywordParser('FILE_DEPENDENCIES:',
                                                       lit.TestRunner.ParserKind.LIST,
                                                       initial_value=fileDependencies),
            lit.TestRunner.IntegratedTestKeywordParser('ADDITIONAL_COMPILE_FLAGS:',
                                                       lit.TestRunner.ParserKind.LIST,
                                                       initial_value=additionalCompileFlags)
        ]

        script = list(steps)
        parsed = lit.TestRunner.parseIntegratedTestScript(test, additional_parsers=parsers,
                                                                require_script=not script)
        if isinstance(parsed, lit.Test.Result):
            return parsed
        script += parsed

        if litConfig.noExecute:
            return lit.Test.Result(lit.Test.PASS)

        # Add compile flags specified with ADDITIONAL_COMPILE_FLAGS.
        self.addCompileFlags(test.config, *additionalCompileFlags)

        tmpDir, tmpBase = lit.TestRunner.getTempPaths(test)
        useExternalSh = True
        substitutions = lit.TestRunner.getDefaultSubstitutions(test, tmpDir, tmpBase,
                                                               normalize_slashes=useExternalSh)

        # Perform substitutions inside FILE_DEPENDENCIES lines (or injected dependencies).
        # This allows using variables like %t in file dependencies. Also note that we really
        # need to resolve %{file_dependencies} now, because otherwise we won't be able to
        # make all paths absolute below.
        fileDependencies = lit.TestRunner.applySubstitutions(fileDependencies, substitutions,
                                                             recursion_limit=test.config.recursiveExpansionLimit)

        # Add the %{file_dependencies} substitution before we perform substitutions
        # inside the script.
        testDir = os.path.dirname(test.getSourcePath())
        fileDependencies = [f if os.path.isabs(f) else os.path.join(testDir, f) for f in fileDependencies]
        substitutions.append(('%{file_dependencies}', ' '.join(map(pipes.quote, fileDependencies))))

        # Perform substitution in the script itself.
        script = lit.TestRunner.applySubstitutions(script, substitutions,
                                                   recursion_limit=test.config.recursiveExpansionLimit)

        return lit.TestRunner._runShTest(test, litConfig, useExternalSh, script, tmpBase)
