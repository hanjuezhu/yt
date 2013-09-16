"""
Data structures for MOAB Hex8.



"""

#-----------------------------------------------------------------------------
# Copyright (c) 2013, yt Development Team.
#
# Distributed under the terms of the Modified BSD License.
#
# The full license is in the file COPYING.txt, distributed with this software.
#-----------------------------------------------------------------------------

import h5py
import numpy as np
import weakref
from yt.funcs import *
from yt.data_objects.grid_patch import \
           AMRGridPatch
from yt.geometry.grid_geometry_handler import \
           GridGeometryHandler
from yt.data_objects.static_output import \
           StaticOutput
from yt.utilities.lib import \
    get_box_grids_level
from yt.utilities.io_handler import \
    io_registry
from yt.utilities.definitions import \
    mpc_conversion, sec_conversion

from .fields import MoabFieldInfo, KnownMoabFields
from yt.data_objects.field_info_container import \
    FieldInfoContainer, NullFunc
import pdb

def _get_convert(fname):
    def _conv(data):
        return data.convert(fname)
    return _conv

class MoabHex8Grid(AMRGridPatch):
    _id_offset = 0
    def __init__(self, id, hierarchy, level, start, dimensions):
        AMRGridPatch.__init__(self, id, filename = hierarchy.hierarchy_filename,
                              hierarchy = hierarchy)
        self.Parent = []
        self.Children = []
        self.Level = level
        self.start_index = start.copy()
        self.stop_index = self.start_index + dimensions
        self.ActiveDimensions = dimensions.copy()

    def _setup_dx(self):
        # So first we figure out what the index is.  We don't assume
        # that dx=dy=dz , at least here.  We probably do elsewhere.
        id = self.id - self._id_offset
        if len(self.Parent) > 0:
            self.dds = self.Parent[0].dds / self.pf.refine_by
        else:
            LE, RE = self.hierarchy.grid_left_edge[id,:], \
                     self.hierarchy.grid_right_edge[id,:]
            self.dds = np.array((RE-LE)/self.ActiveDimensions)
        if self.pf.dimensionality < 2: self.dds[1] = 1.0
        if self.pf.dimensionality < 3: self.dds[2] = 1.0
        self.field_data['dx'], self.field_data['dy'], self.field_data['dz'] = self.dds

    @property
    def filename(self):
        return None

class MoabHex8Hierarchy(GridGeometryHandler):

    grid = MoabHex8Grid

    def __init__(self, pf, data_style='h5m'):
        self.parameter_file = weakref.proxy(pf)
        self.data_style = data_style
        self.max_level = 10  # FIXME
        # for now, the hierarchy file is the parameter file!
        self.hierarchy_filename = self.parameter_file.parameter_filename
        self.directory = os.path.dirname(self.hierarchy_filename)
        self._fhandle = h5py.File(self.hierarchy_filename,'r')
        GridGeometryHandler.__init__(self,pf,data_style)

        self._fhandle.close()

    def _initialize_data_storage(self):
        pass

    def _detect_fields(self):
        self.field_list = self._fhandle['field_types'].keys()

    def _setup_classes(self):
        dd = self._get_data_reader_dict()
        GridGeometryHandler._setup_classes(self, dd)
        self.object_types.sort()

    def _count_grids(self):
        self.num_grids = 1 #self._fhandle['/grid_parent_id'].shape[0]

    def _parse_hierarchy(self):
        f = self._fhandle
        dxs = []
        self.grids = np.empty(self.num_grids, dtype='object')
        levels = [0]
        glis = (f['grid_left_index'][:]).copy()
        gdims = (f['grid_dimensions'][:]).copy()
        active_dims = ~((np.max(gdims, axis=0) == 1) &
                        (self.parameter_file.domain_dimensions == 1))

        for i in range(levels.shape[0]):
            self.grids[i] = self.grid(i, self, levels[i],
                                      glis[i],
                                      gdims[i])
            self.grids[i]._level_id = levels[i]

            dx = (self.parameter_file.domain_right_edge-
                  self.parameter_file.domain_left_edge)/self.parameter_file.domain_dimensions
            dx[active_dims] = dx[active_dims]/self.parameter_file.refine_by**(levels[i])
            dxs.append(dx)
        dx = np.array(dxs)
        self.grid_left_edge = self.parameter_file.domain_left_edge + dx*glis
        self.grid_dimensions = gdims.astype("int32")
        self.grid_right_edge = self.grid_left_edge + dx*self.grid_dimensions
        self.grid_particle_count = f['grid_particle_count'][:]
        del levels, glis, gdims

    def _populate_grid_objects(self):
        mask = np.empty(self.grids.size, dtype='int32')
        for gi, g in enumerate(self.grids):
            g._prepare_grid()
            g._setup_dx()

        for gi, g in enumerate(self.grids):
            g.Children = self._get_grid_children(g)
            for g1 in g.Children:
                g1.Parent.append(g)
            get_box_grids_level(self.grid_left_edge[gi,:],
                                self.grid_right_edge[gi,:],
                                self.grid_levels[gi],
                                self.grid_left_edge, self.grid_right_edge,
                                self.grid_levels, mask)
            m = mask.astype("bool")
            m[gi] = False
            siblings = self.grids[gi:][m[gi:]]
            if len(siblings) > 0:
                g.OverlappingSiblings = siblings.tolist()
        self.max_level = self.grid_levels.max()

    def _setup_derived_fields(self):
        self.derived_field_list = []

    def _get_box_grids(self, left_edge, right_edge):
        '''
        Gets back all the grids between a left edge and right edge
        '''
        eps = np.finfo(np.float64).eps
        grid_i = np.where((np.all((self.grid_right_edge - left_edge) > eps, axis=1) \
                        &  np.all((right_edge - self.grid_left_edge) > eps, axis=1)) == True)

        return self.grids[grid_i], grid_i


    def _get_grid_children(self, grid):
        mask = np.zeros(self.num_grids, dtype='bool')
        grids, grid_ind = self._get_box_grids(grid.LeftEdge, grid.RightEdge)
        mask[grid_ind] = True
        return [g for g in self.grids[mask] if g.Level == grid.Level + 1]

    def _setup_data_io(self):
        self.io = io_registry[self.data_style](self.parameter_file)


class MoabHex8StaticOutput(StaticOutput):
    _hierarchy_class = MoabHex8Hierarchy
    _fieldinfo_fallback = MoabFieldInfo
    _fieldinfo_known = KnownMoabFields

    def __init__(self, filename, data_style='grid_data_format',
                 storage_filename = None):
        StaticOutput.__init__(self, filename, data_style)
        self.storage_filename = storage_filename
        self.filename = filename

    def _set_units(self):
        """Generates the conversion to various physical _units based on the parameter file
        """
        self.units = {}
        self.time_units = {}
        if len(self.parameters) == 0:
            self._parse_parameter_file()
        self.time_units['1'] = 1
        self.units['1'] = 1.0
        self.units['cm'] = 1.0
        self.units['unitary'] = 1.0 / (self.domain_right_edge - self.domain_left_edge).max()
        for unit in mpc_conversion.keys():
            self.units[unit] = 1.0 * mpc_conversion[unit] / mpc_conversion["cm"]
        for unit in sec_conversion.keys():
            self.time_units[unit] = 1.0 / sec_conversion[unit]

        # This should be improved.
        self._handle = h5py.File(self.parameter_filename, "r")
        """\
        for field_name in self._handle["/field_types"]:
            current_field = self._handle["/field_types/%s" % field_name]
            if 'field_to_cgs' in current_field.attrs:
                self.units[field_name] = current_field.attrs['field_to_cgs']
            else:
                self.units[field_name] = 1.0
            if 'field_units' in current_field.attrs:
                current_fields_unit = just_one(current_field.attrs['field_units'])
            else:
                current_fields_unit = ""
            self._fieldinfo_known.add_field(field_name, function=NullFunc, take_log=False,
                   units=current_fields_unit, projected_units="",
                   convert_function=_get_convert(field_name))
        """
        self._handle.close()
        del self._handle

    def _parse_parameter_file(self):
        self._handle = f = h5py.File(self.parameter_filename, "r")
        coords = self._handle["/tstt/nodes/coordinates"]
        self.domain_left_edge = coords[0]
        self.domain_right_edge = coords[-1]
        self.domain_dimensions = self.domain_right_edge - self.domain_left_edge
        self.refine_by = 2
        self.dimensionality = len(self.domain_dimensions)
        self.current_time = 0.0
        self.unique_identifier = self.parameter_filename
        self.cosmological_simulation = False
        self.num_ghost_zones = 0
        #self.field_ordering = sp["field_ordering"]
        #self.boundary_conditions = sp["boundary_conditions"][:]
        #p = [bnd == 0 for bnd in self.boundary_conditions[::2]]
        #self.periodicity = ensure_tuple(p)
        self.current_redshift = self.omega_lambda = self.omega_matter \
                              = self.hubble_constant \
                              = self.cosmological_simulation = 0.0
        self.parameters['Time'] = 1.0 # Hardcode time conversion for now.
        self.parameters["HydroMethod"] = 0 # Hardcode for now until field staggering is supported.
        self._handle.close()
        del self._handle

    @classmethod
    def _is_valid(self, *args, **kwargs):
        fname = args[0]
        return fname.endswith('.h5m')

    def __repr__(self):
        return self.basename.rsplit(".", 1)[0]
