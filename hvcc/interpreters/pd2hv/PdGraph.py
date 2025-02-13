# Copyright (C) 2014-2018 Enzien Audio, Ltd.
# Copyright (C) 2023 Wasted Audio
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import os
from typing import Optional, List, Dict, Any

from .Connection import Connection
from .NotificationEnum import NotificationEnum
from .PdObject import PdObject


class PdGraph(PdObject):

    def __init__(
        self,
        obj_args: List,
        pd_path: str,
        pos_x: int = 0,
        pos_y: int = 0
    ) -> None:
        assert len(obj_args) > 0, "PdGraph arguments must contain at least dollar zero."
        super().__init__("graph", obj_args, pos_x, pos_y)

        # file location of this graph
        self.__pd_path = pd_path

        self.__objs: List[PdObject] = []
        self.__connections: List[Connection] = []

        self.__inlet_objects: List = []
        self.__outlet_objects: List = []

        # the first search path is always the directory of this graph
        self.__declared_paths: List = [os.path.dirname(pd_path)]

        # heavy graph arguments (added via @hv_arg flag in #X text)
        self.hv_args: List = []

        # the subpatch name of this graph
        # only used is this graph is actually a subpatch
        self.subpatch_name: Optional[str] = None

        # TODO(dromer) these are virtual attributes that are only instantiated with internal representation
        self._PdGraph__connections: List[Connection] = []
        self._PdGraph__pd_path: str = ""

    @property
    def dollar_zero(self) -> str:
        return self.obj_args[0]

    @property
    def is_root(self) -> bool:
        return self.parent_graph is None

    @property
    def is_subpatch(self) -> bool:
        if self.parent_graph is None:
            return False
        else:
            return self.parent_graph.__pd_path == self.__pd_path if not self.is_root else False

    def add_object(self, obj: PdObject) -> int:
        obj.parent_graph = self
        self.__objs.append(obj)

        if obj.obj_type in ["inlet", "inlet~"]:
            self.__inlet_objects.append(obj)
            # set correct let index by sorting on x position
            self.__inlet_objects.sort(key=lambda o: o.pos_x)
            for i, o in enumerate(self.__inlet_objects):
                o.let_index = i
        elif obj.obj_type in ["outlet", "outlet~"]:
            self.__outlet_objects.append(obj)
            self.__outlet_objects.sort(key=lambda o: o.pos_x)
            for i, o in enumerate(self.__outlet_objects):
                o.let_index = i

        return (len(self.__objs) - 1)

    def add_parsed_connection(self, from_index: int, from_outlet: int, to_index: int, to_inlet: int) -> None:
        """ Add a connection to the graph which has been parsed externally.
        """
        try:
            # when connecting a Heavy object that allows mixed connection types,
            # try to immediately resolve to a non-mixed type based on the
            # object that it is connected to.
            connection_type = self.__objs[from_index].get_outlet_connection_type(from_outlet)
            if connection_type == "-~>":
                connection_type = self.__objs[to_index].get_inlet_connection_type(to_inlet)

            # make the connection
            c = Connection(
                self.__objs[from_index], from_outlet,
                self.__objs[to_index], to_inlet,
                connection_type)
            self.__connections.append(c)  # update the local connections list

            # allow the connected objects to keep track of their own connections
            # (generally used for reporting and validation purposes)
            self.__objs[from_index].add_connection(c)
            self.__objs[to_index].add_connection(c)
        except Exception:
            self.add_error("There was an error while connecting two objects. "
                           "Have all objects been correctly instantiated? "
                           "Have all inlets and outlets been declared?",
                           NotificationEnum.ERROR_UNABLE_TO_CONNECT_OBJECTS)

    def add_hv_arg(
        self,
        arg_index: int,
        name: str,
        value_type: str,
        default_value: Optional[Any],
        required: bool
    ) -> None:
        """ Add a Heavy argument to the graph. Indicies are from zero (not one, like Pd).
        """
        # ensure that self.hv_args is big enough, as heavy arguments are not
        # necessarily added in the natural order
        while arg_index >= len(self.hv_args):
            self.hv_args.append(None)

        self.hv_args[arg_index] = {
            "name": name,
            "description": "",
            "value_type": value_type,
            "default": default_value,
            "required": required
        }

    def get_object(self, obj_index: int) -> PdObject:
        return self.__objs[obj_index]

    def get_objects(self) -> List[PdObject]:
        return self.__objs

    def get_inlet_connection_type(self, inlet_index: int) -> str:
        return self.__inlet_objects[inlet_index].get_inlet_connection_type(inlet_index)

    def get_outlet_connection_type(self, outlet_index: int) -> str:
        return self.__outlet_objects[outlet_index].get_outlet_connection_type(outlet_index)

    def validate_configuration(self) -> None:
        if self.is_root:
            if any((o.obj_type in {"inlet~", "outlet~"}) for o in self.__objs):
                self.add_error(
                    "Top-level graphs may not contain inlet~ or outlet~ objects. "
                    "Use adc~ and dac~.",
                    NotificationEnum.ERROR_NO_TOPLEVEL_SIGNAL_LETS)
            if any((o.obj_type in {"inlet", "outlet"}) for o in self.__objs):
                self.add_warning(
                    "Control inlets and outlets in top-level graphs don't do "
                    "anything. Use receive and send objects.")

        # validate all object recursively
        for o in self.__objs:
            o.validate_configuration()

    def is_abstraction_on_call_stack(self, abs_path: str) -> bool:
        """ Returns True if the given abstraction name is already on the call
            stack (i.e. it is currently being parsed). This function is used to
            detect recursion within abstractions.
        """
        if self.__pd_path == abs_path:
            return True
        elif not self.is_root and self.parent_graph:
            return self.parent_graph.is_abstraction_on_call_stack(abs_path)
        else:
            return False

    def get_notices(self) -> Dict:
        notices = PdObject.get_notices(self)
        for o in self.__objs:
            n = o.get_notices()
            notices["warnings"].extend(n["warnings"])
            notices["errors"].extend(n["errors"])

        # remove ERROR_EXCEPTION if there are already other errors.
        # The exception is always a result of some other error
        if any((n["enum"] != NotificationEnum.ERROR_EXCEPTION) for n in notices["errors"]):
            notices["errors"] = [n for n in notices["errors"] if n["enum"] != NotificationEnum.ERROR_EXCEPTION]

        return notices

    def get_graph_heirarchy(self) -> List:
        """ Returns the "path" of this graph, indicating where it is in the
            graph heirarchy (i.e. with file names, etc.)
        """
        if self.is_root:
            return [str(self)]
        elif self.parent_graph is not None:
            return self.parent_graph.get_graph_heirarchy() + [str(self)]
        else:
            # NOTE(dromer): we should never get here
            raise Exception("parent_graph argument is None")

    def get_depth(self) -> int:
        """ Returns the depth of this graph, with the root being at 1.
        """
        if self.is_root:
            return 1
        elif self.parent_graph is not None:
            return 1 + self.parent_graph.get_depth()
        else:
            # NOTE(dromer): we should never get here
            raise Exception("parent_graph argument is None")

    def to_hv(self, export_args: bool = False) -> Dict:
        # NOTE(mhroth): hv_args are not returned. Because all arguments have
        # been resolved, no arguments are otherwise passed. hv2ir would break
        # on required arguments that are not passed to the graph
        assert all(a is not None for a in self.hv_args), "Graph is missing a @hv_arg."
        return {
            "type": "graph",
            "imports": [],
            "args": self.hv_args if export_args else [],
            "objects": {o.obj_id: o.to_hv() for o in self.__objs},
            "connections": [c.to_hv() for c in self.__connections],
            "properties": {
                "x": self.pos_x,
                "y": self.pos_y
            }
        }

    def __repr__(self) -> str:
        return self.subpatch_name or os.path.basename(self.__pd_path)
