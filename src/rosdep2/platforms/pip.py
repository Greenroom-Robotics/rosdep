# Copyright (c) 2009, Willow Garage, Inc.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#     * Redistributions of source code must retain the above copyright
#       notice, this list of conditions and the following disclaimer.
#     * Redistributions in binary form must reproduce the above copyright
#       notice, this list of conditions and the following disclaimer in the
#       documentation and/or other materials provided with the distribution.
#     * Neither the name of the Willow Garage, Inc. nor the names of its
#       contributors may be used to endorse or promote products derived from
#       this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

# Author Tully Foote/tfoote@willowgarage.com

from __future__ import print_function

import os
import subprocess
import sys
from packaging.version import Version

try:
    import importlib.metadata as importlib_metadata
except ImportError:
    import importlib_metadata

from ..core import InstallFailed, InvalidData
from ..installers import PackageManagerInstaller
from ..shell_utils import read_stdout

# pip package manager key
PIP_INSTALLER = 'pip'


def register_installers(context):
    context.set_installer(PIP_INSTALLER, PipInstaller())


def get_pip_command():
    # First try pip2 or pip3
    cmd = ['pip' + os.environ['ROS_PYTHON_VERSION']]
    if is_cmd_available(cmd):
        return cmd

    # Second, try using the same python executable since we know that exists
    if os.environ['ROS_PYTHON_VERSION'] == sys.version[0]:
        try:
            import pip
        except ImportError:
            pass
        else:
            return [sys.executable, '-m', 'pip']

    # Finally, try python2 or python3 commands
    cmd = ['python' + os.environ['ROS_PYTHON_VERSION'], '-m', 'pip']
    if is_cmd_available(cmd):
        return cmd
    return None


def is_cmd_available(cmd):
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        _ = proc.communicate()
        return 0 == proc.returncode
    except OSError:
        return False


def pip_detect(pkgs, exec_fn=None):
    """
    Given a list of package, return the list of installed packages.

    :param exec_fn: function to execute Popen and read stdout (for testing)
    """
    pip_cmd = get_pip_command()
    if not pip_cmd:
        return []

    fallback_to_pip_show = False
    if exec_fn is None:
        exec_fn = read_stdout
        fallback_to_pip_show = True
    pkg_list = exec_fn(pip_cmd + ['freeze']).split('\n')

    ret_list = []
    for pkg in pkg_list:
        pkg_row = pkg.split('==')
        if pkg_row[0] in pkgs:
            ret_list.append(pkg_row[0])

    # Try to detect with the return code of `pip show`.
    # This can show the existance of things like `argparse` which
    # otherwise do not show up.
    # See:
    #   https://github.com/pypa/pip/issues/1570#issuecomment-71111030
    if fallback_to_pip_show:
        for pkg in [p for p in pkgs if p not in ret_list]:
            # does not see retcode but stdout for old pip to check if installed
            pkg_name = pkg
            if any(x in pkg for x in ['>', '<', '=']):
                import re
                pkg_name = re.split('[^a-zA-Z0-9]', pkg)[0]
            proc = subprocess.Popen(
                pip_cmd + ['show', pkg_name],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT
            )
            output, _ = proc.communicate()
            output = output.strip()
            if proc.returncode == 0 and output:
                # `pip show` detected it, add it to the list.
                ret_list.append(pkg)

    return ret_list


class PipInstaller(PackageManagerInstaller):
    """
    :class:`Installer` support for pip.
    """

    def __init__(self):
        super(PipInstaller, self).__init__(pip_detect, supports_depends=True)

    def resolve(self, rosdep_args, rosdep=None):
        """
        See :meth:`Installer.resolve()`
        """
        packages = None
        if type(rosdep_args) == dict:
            packages = rosdep_args.get('packages', [])
            if isinstance(packages, str):
                packages = packages.split()

        elif isinstance(rosdep_args, str):
            packages = rosdep_args.split(' ')
        elif type(rosdep_args) == list:
            packages = rosdep_args
        else:
            raise InvalidData('Invalid rosdep args: %s' % (rosdep_args))

        if rosdep:
            pip_specify_version = None
            if rosdep.version_eq:
                for i, package in list(enumerate(packages)):
                    packages[i] = package + '==' + rosdep.version_eq
                pip_specify_version = True
            if rosdep.version_gte:
                for i, package in list(enumerate(packages)):
                    package = package + ',' if pip_specify_version else package
                    packages[i] = package + '>=' + rosdep.version_gte
                pip_specify_version = True
            if rosdep.version_lte:
                for i, package in list(enumerate(packages)):
                    package = package + ',' if pip_specify_version else package
                    packages[i] = package + '<=' + rosdep.version_lte
                pip_specify_version = True
            if rosdep.version_gt:
                for i, package in list(enumerate(packages)):
                    package = package + ',' if pip_specify_version else package
                    packages[i] = package + '>' + rosdep.version_gt
                pip_specify_version = True
            if rosdep.version_lt:
                for i, package in list(enumerate(packages)):
                    package = package + ',' if pip_specify_version else package
                    packages[i] = package + '<' + rosdep.version_lt
                pip_specify_version = True
            if pip_specify_version:
                for i, package in list(enumerate(packages)):
                    packages[i] = package

        return packages

    def get_version_strings(self):
        pip_version = importlib_metadata.version('pip')
        # keeping the name "setuptools" for backward compatibility
        setuptools_version = importlib_metadata.version('setuptools')
        version_strings = [
            'pip {}'.format(pip_version),
            'setuptools {}'.format(setuptools_version),
        ]
        return version_strings

    def get_install_command(self, resolved, interactive=True, reinstall=False, quiet=False):
        pip_cmd = get_pip_command()
        if not pip_cmd:
            raise InstallFailed((PIP_INSTALLER, 'pip is not installed'))
        packages = self.get_packages_to_install(resolved, reinstall=reinstall)
        if not packages:
            return []
        cmd = pip_cmd + ['install']
        if Version(importlib_metadata.version('pip')) >= Version("24.0"):
            cmd += ["--break-system-packages"]
        if quiet:
            cmd.append('-q')
        if reinstall:
            cmd.append('-I')
        return [self.elevate_priv(cmd + [p] if any(x in p for x in ['>', '<', '=']) else cmd + ['-U'] + [p]) for p in packages]
