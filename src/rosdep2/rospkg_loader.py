# Copyright (c) 2011, Willow Garage, Inc.
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

# Author Ken Conley/kwc@willowgarage.com

"""
Library for loading rosdep files from the ROS package/stack
filesystem.
"""

from __future__ import print_function

import os

import catkin_pkg.package
import rospkg

from .catkin_packages import VALID_DEPENDENCY_TYPES
from .loader import RosdepLoader

# Default view key is the view that packages that are not in stacks
# see. It is the root of all dependencies.  It is superceded by an
# explicit underlay_key.
DEFAULT_VIEW_KEY = '*default*'

# Implementation details: this API was originally conceived under the
# rosdep 1 design.  It has since been retrofitted for the rosdep 2
# design, which means it is a bit overbuilt.  There really is no need
# for a notion of views for rospkg -- all rospkgs have the same view.
# It we be nice to refactor this API into something much, much
# simpler, which would probably involve merging RosPkgLoader and
# SourcesListLoader.  RosPkgLoader would provide identification of
# resources and SourcesListLoader would build a *single* view that was
# no longer resource-dependent.


class RosPkgLoader(RosdepLoader):

    def __init__(self, rospack=None, rosstack=None, underlay_key=None, dependency_types=[]):
        """
        :param underlay_key: If set, all views loaded by this loader
            will depend on this key.
        """
        if rospack is None:
            rospack = rospkg.RosPack()
        if rosstack is None:
            rosstack = rospkg.RosStack()

        self._rospack = rospack
        self._rosstack = rosstack
        self._rosdep_yaml_cache = {}
        self._underlay_key = underlay_key

        # cache computed list of loadable resources
        self._loadable_resource_cache = None
        self._catkin_packages_cache = None

        default_dep_types = VALID_DEPENDENCY_TYPES - {'doc'}
        self.include_dep_types = VALID_DEPENDENCY_TYPES.intersection(set(dependency_types)) if dependency_types else default_dep_types

    def load_view(self, view_name, rosdep_db, verbose=False):
        """
        Load view data into *rosdep_db*. If the view has already
        been loaded into *rosdep_db*, this method does nothing.  If
        view has no rosdep data, it will be initialized with an empty
        data map.

        :raises: :exc:`InvalidData` if view rosdep.yaml is invalid
        :raises: :exc:`rospkg.ResourceNotFound` if view cannot be located

        :returns: ``True`` if view was loaded.  ``False`` if view
          was already loaded.
        """
        if rosdep_db.is_loaded(view_name):
            return
        if view_name not in self.get_loadable_views():
            raise rospkg.ResourceNotFound(view_name)
        elif view_name == 'invalid':
            raise rospkg.ResourceNotFound('FOUND' + view_name + str(self.get_loadable_views()))
        if verbose:
            print('loading view [%s] with rospkg loader' % (view_name))
        # chain into underlay if set
        if self._underlay_key:
            view_dependencies = [self._underlay_key]
        else:
            view_dependencies = []
        # no rospkg view has actual data
        rosdep_db.set_view_data(view_name, {}, view_dependencies, '<nodata>')

    def get_loadable_views(self):
        """
        'Views' map to ROS stack names.
        """
        return list(self._rosstack.list()) + [DEFAULT_VIEW_KEY]

    def get_loadable_resources(self):
        """
        'Resources' map to ROS packages names.
        """
        if not self._loadable_resource_cache:
            self._loadable_resource_cache = list(self._rospack.list())
        return self._loadable_resource_cache

    def get_catkin_paths(self):
        if not self._catkin_packages_cache:
            def find_catkin_paths(src):
                return [(x, src.get_path(x)) for x in
                        filter(lambda x: src.get_manifest(x).is_catkin, src.list())]
            self._catkin_packages_cache = dict(find_catkin_paths(self._rospack))
            self._catkin_packages_cache.update(find_catkin_paths(self._rosstack))
        return self._catkin_packages_cache

    def get_rosdeps(self, resource_name, implicit=True):
        """
        If *resource_name* is a stack, returns an empty list.

        :raises: :exc:`rospkg.ResourceNotFound` if *resource_name* cannot be found.
        """
        if resource_name in self.get_catkin_paths():
            pkg = catkin_pkg.package.parse_package(self.get_catkin_paths()[resource_name])
            pkg.evaluate_conditions(os.environ)
            deps = sum((getattr(pkg, '{}_depends'.format(d)) for d in self.include_dep_types), [])
            return [d for d in deps if d.evaluated_condition]
        elif resource_name in self.get_loadable_resources():
            # expand 'self._rospack.get_rosdeps(resource_name, implicit=implicit)' to return Dependency
            def rospack_get_rosdeps(rospack, package, implicit=implicit):
                if implicit:
                    return rospack_implicit_rosdeps(rospack, package)
                else:
                    m = rospack.get_manifest(package)
                    return m.rosdeps

            def rospack_implicit_rosdeps(rospack, package):
                # set the key before recursive call to prevent infinite case
                s = set()
                # take the union of all dependencies
                packages = rospack.get_depends(package, implicit=True)
                for p in packages:
                    s.update(rospack_get_rosdeps(rospack, p, implicit=False))
                # add in our own deps
                m = rospack.get_manifest(package)
                s.update(m.rosdeps)
                # cache the return value as a list
                s = list(s)
                return s

            rosdeps = set(rospack_get_rosdeps(self._rospack, resource_name, implicit=False))
            if implicit:
                # This resource is a manifest.xml, but it might depend on things with a package.xml
                # Make sure they get a chance to evaluate conditions
                for dep in self._rospack.get_depends(resource_name):
                    rosdeps = rosdeps.union(set(rospack_get_rosdeps(self._rospack, dep, implicit=True)))
            return list(rosdeps)
        elif resource_name in self._rosstack.list():
            # stacks currently do not have rosdeps of their own, implicit or otherwise
            return []
        else:
            raise rospkg.ResourceNotFound(resource_name)

    def is_metapackage(self, resource_name):
        if resource_name in self._rosstack.list():
            m = self._rosstack.get_manifest(resource_name)
            return m.is_catkin
        return False

    def get_view_key(self, resource_name):
        """
        Map *resource_name* to a view key.  In rospkg, this maps the
        DEFAULT_VIEW_KEY if *resource_name* exists.

        :raises: :exc:`rospkg.ResourceNotFound`
        """
        if (
            resource_name in self.get_catkin_paths() or
            resource_name in self.get_loadable_resources()
        ):
            return DEFAULT_VIEW_KEY
        else:
            raise rospkg.ResourceNotFound(resource_name)
