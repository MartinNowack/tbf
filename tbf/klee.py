from input_generation import BaseInputGenerator
from test_validation import TestValidator
import utils
import glob
import os
import logging

include_dir = os.path.abspath('./klee/include/')
lib_dir = os.path.abspath('./klee/lib')
bin_dir = os.path.abspath('./klee/bin')
tests_output = utils.tmp
tests_dir = os.path.join(tests_output, 'klee-tests')
klee_make_symbolic = 'klee_make_symbolic'
name = 'klee'


def get_test_cases(exclude=[], directory=tests_dir):
    all_tests = [t for t in glob.glob(directory + '/*.ktest')]
    tcs = list()
    for t in [t for t in all_tests if utils.get_file_name(t) not in exclude]:
        print(t)
        file_name = utils.get_file_name(t)
        with open(t, mode='rb') as inp:
            content = inp.read()
        tcs.append(utils.TestCase(file_name, t, content))
    return tcs


class InputGenerator(BaseInputGenerator):

    def __init__(self, timelimit=0, log_verbose=False, search_heuristic=['random-path', 'nurs:covnew'], machine_model=utils.MACHINE_MODEL_32):
        super().__init__(timelimit, machine_model, log_verbose)
        self.log_verbose = log_verbose
        if type(search_heuristic) is not list:
            self.search_heuristic = list(search_heuristic)
        else:
            self.search_heuristic = search_heuristic

        self._run_env = utils.get_env_with_path_added(bin_dir)

    def get_run_env(self):
        return self._run_env

    def get_name(self):
        return name

    def prepare(self, filecontent, nondet_methods_used):
        content = filecontent
        content += '\n'
        for method in nondet_methods_used:  # append method definition at end of file content
            nondet_method_definition = self._get_nondet_method(method)
            content += nondet_method_definition
        return content

    def _get_nondet_method(self, method_information):
        method_name = method_information['name']
        m_type = method_information['type']
        param_types = method_information['params']
        return self._create_nondet_method(method_name, m_type, param_types)

    def _create_nondet_method(self, method_name, method_type, param_types):
        var_name = utils.get_sym_var_name(method_name)
        method_head = utils.get_method_head(method_name, method_type, param_types)
        method_body = ['{']
        if method_type != 'void':
            method_body += ['{0} {1};'.format(method_type, var_name),
                            'klee_make_symbolic(&{0}, sizeof({0}), \"{0}\");'.format(var_name),
                            'return {0};'.format(var_name)
                            ]
        method_body = '\n    '.join(method_body)
        method_body += '\n}\n'

        return method_head + method_body

    def create_input_generation_cmds(self, filename):
        if self.machine_model.is_32:
            mm_args = ['-arch', 'i386']
        elif self.machine_model.is_64:
            mm_args = ['-arch', 'x86_64']
        else:
            raise AssertionError("Unhandled machine model: " + self.machine_model.name)

        compiled_file = '.'.join(os.path.basename(filename).split('.')[:-1] + ['bc'])
        compiled_file = utils.get_file_path(compiled_file, temp_dir=True)
        compile_cmd = ['clang'] + mm_args + ['-I', include_dir, '-emit-llvm', '-c', '-g', '-o', compiled_file, filename]
        input_generation_cmd = ['klee']
        if self.timelimit > 0:
            input_generation_cmd += ['-max-time', str(self.timelimit)]
        input_generation_cmd.append('-only-output-states-covering-new')
        input_generation_cmd += ['-search=' + h for h in self.search_heuristic]
        input_generation_cmd += ['-output-dir=' + tests_dir]
        input_generation_cmd += [compiled_file]

        return [compile_cmd, input_generation_cmd]

    def get_test_count(self):
        files = get_test_cases()
        if not files:
            raise utils.InputGenerationError('No test files generated.')
        return len(files)


class KleeTestValidator(TestValidator):

    def get_name(self):
        return name

    def _get_var_number(self, test_info_line):
        assert 'object' in test_info_line
        return test_info_line.split(':')[0].split(' ')[-1]  # Object number should be at end, e.g. 'object  1: ...'

    def _get_test_vector(self, test):
        def _get_value(single_line):
            var_name = single_line.split(':')[2].strip()
            prefix_end = var_name.find("'")
            var_name = var_name[prefix_end+1:-1]
            return var_name

        ktest_tool = [os.path.join(bin_dir, 'ktest-tool')]
        exec_output = utils.execute(ktest_tool + [test.origin], err_to_output=False, quiet=True)
        test_info = exec_output.stdout.split('\n')
        vector = utils.TestVector(test.name, test.origin)
        last_number = -1
        last_nondet_method = None
        last_value = None
        for line in [l for l in test_info if l.startswith('object')]:
            logging.debug("Looking at line: %s", line)
            if 'name:' in line:
                #assert len(line.split(':')) == 3
                var_number = int(self._get_var_number(line))
                assert var_number > last_number
                last_number = var_number
                var_name = _get_value(line)
                assert last_nondet_method is None, \
                        "Last nondet method already or still assigned: %s" % last_nondet_method
                assert "'" not in var_name, \
                        "Variable name contains \"'\": %s" % var_name
                last_nondet_method = utils.get_corresponding_method_name(var_name)
            elif 'data:' in line:
                #assert len(line.split(':')) == 3
                var_number = self._get_var_number(line)
                assert last_nondet_method is not None
                value = _get_value(line)
                value, = utils.convert_to_int(value, last_nondet_method)
                assert last_value is None
                last_value = str(value)
            if last_nondet_method is not None and last_value is not None:
                vector.add(last_value, last_nondet_method)
                last_nondet_method = None
                last_value = None

        return vector

    def get_test_cases(self, exclude=[]):
        return get_test_cases(exclude)

