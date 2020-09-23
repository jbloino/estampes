#!/usr/bin/env python3

"""
    ESTAMPES: Experimental Support Toolbox for the Analysis, Modelling,
              Plotting and Elucidation of Spectra.

This is the main program for data parser, which also acts as an
  illustration of the toolbox.
"""

import sys
import os
import argparse
import typing as tp

import numpy as np
import matplotlib.pyplot as plt
from PySide2 import QtCore, QtGui
from PySide2.Qt3DCore import Qt3DCore
from PySide2.Qt3DRender import Qt3DRender
from PySide2.Qt3DExtras import Qt3DExtras

from estampes.base import QuantityError, TypeColor
from estampes.parser import DataFile, build_qlabel
from estampes.data.physics import PHYSFACT
from estampes.tools.atom import convert_labsymb
from estampes.visual.molview import list_bonds, Molecule, TypeAtLabM, \
    TypeAtCrdM, TypeBondsM, MOLCOLS
from estampes.visual.plotmat import plot_jmat, plot_cmat, plot_kvec
from estampes.visual.plotspec import plot_spec_2D

FCHT_QTIES = {
    'mols': {
        'atnum': build_qlabel('atnum'),
        'IniS': build_qlabel('fcdat', 'GeomIS'),
        'FinS': build_qlabel('fcdat', 'GeomFS'),
        'MidS': build_qlabel('fcdat', 'GeomMS'),
        'ExtG': build_qlabel('fcdat', 'ExGeom')
    },
    'jmat': {
        'JMat': build_qlabel('fcdat', 'JMat')
    },
    'fulljmat': {
        'JFul': build_qlabel('fcdat', 'JMatF')
    },
    'cmat': {
        'CMat': build_qlabel('fcdat', 'CMat')
    },
    'kvec': {
        'KVec': build_qlabel('fcdat', 'KVec')
    },
    'spec': {
        'Spec': build_qlabel('fcdat', 'Spec'),
        'Pars': build_qlabel('fcdat', 'SpcPar'),
    }
}


class MolWin(Qt3DExtras.Qt3DWindow):
    """Qt3D Window for the visualization of molecule(s)

    Attributes
    ----------
    nmols
        Number of molecules stored in `atlabs`, `atcrds` and `bonds`.
    atlabs
        Atomic labels.
        If `nmols>1`, list of lists.
    atcrds
        3-tuples with atomic coordinates, in Ang.
        If `nmols>1`, list of lists.
    bonds
        2-tuples listing connected atoms.
        If `nmols>1`, list of lists.
    col_bond_as_atom
        If true, bonds are colored based on the connected atoms
    rad_atom_as_bond
        If true, atomic radii are set equal to the bonds (tubes).
    molcols
        If not `None`, color of the each molecule.
    """
    def __init__(self, nmols: int,
                 atlabs: TypeAtLabM,
                 atcrds: TypeAtCrdM,
                 bonds: TypeBondsM,
                 col_bond_as_atom: bool = False,
                 rad_atom_as_bond: bool = False,
                 molcols: tp.Optional[TypeColor] = None):
        super(MolWin, self).__init__()

        # Camera
        self.camera().lens().setPerspectiveProjection(45, 16 / 9, 0.1, 1000)
        self.camera().setPosition(QtGui.QVector3D(0, 1, 40))
        self.camera().setViewCenter(QtGui.QVector3D(0, 0, 0))

        # For camera controls
        self.rootEntity = Qt3DCore.QEntity()
        if nmols == 1:
            self.mol = Molecule(atlabs, atcrds, bonds, col_bond_as_atom,
                                rad_atom_as_bond, molcols, self.rootEntity)
            self.mol.addMouse(self.camera)
        else:
            self.mols = []
            for i in range(nmols):
                self.mols.append(Molecule(atlabs[i], atcrds[i], bonds[i],
                                          col_bond_as_atom, rad_atom_as_bond,
                                          molcols[i], self.rootEntity))
                self.mols[-1].addMouse(self.camera)
        self.camController = Qt3DExtras.QOrbitCameraController(self.rootEntity)
        self.camController.setLinearSpeed(50)
        self.camController.setLookSpeed(180)
        self.camController.setCamera(self.camera())
        self.obj_light = Qt3DCore.QEntity(self.camera())
        self.camLight = Qt3DRender.QPointLight(self.obj_light)
        self.cam_tvec = Qt3DCore.QTransform()
        self.cam_tvec.setTranslation(QtGui.QVector3D(0, 50, 100))
        self.obj_light.addComponent(self.camLight)
        self.obj_light.addComponent(self.cam_tvec)
        # self.camLight.setIntensity(100)
        # for mol in mols:
        self.setRootEntity(self.rootEntity)

    def mousePressEvent(self, mouseEvent):
        if (mouseEvent.button() == QtCore.Qt.RightButton):
            self.camera().setViewCenter(QtGui.QVector3D(0, 0, 0))
        # print(mouseEvent.x())
        # self.camera().setViewCenter(QtGui.QVector3D(mouseEvent.x(),
        #                                             mouseEvent.y(), 0))
        super(MolWin, self).mousePressEvent(mouseEvent)


def parse_args(args: tp.Sequence[str]) -> argparse.Namespace:
    """Parses arguments.

    Parses commandline arguments

    Parameters
    ----------
    args
        Commandline arguments

    Returns
    -------
    :obj:`argparse.Namespace`
        Object holding results as attributes
    """
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawTextHelpFormatter)
    # Basic
    psubs = parser.add_subparsers(help='Molecular viewer')
    parser.set_defaults(mode='gui')

    # MolView
    pmol = psubs.add_parser('molview', aliases=['mol'],
                            help='Molecular viewer')
    pmol.add_argument('datafile',
                      help='Data file.')
    pmol.set_defaults(mode='mol')
    # Vibrational spectroscopy
    pvib = psubs.add_parser('vibrational', aliases=['vib', 'l717'],
                            help='Vibrational spectroscopy')
    pvib.add_argument('datafile', help='Data file.')

    pvib.set_defaults(mode='vib')
    # Vibrationally-resolved electronic spectroscopy
    pvel = psubs.add_parser('vibronic', aliases=['FCHT', 'l718'],
                            help='Vibronic specroscopy')
    pvel.add_argument('-o', '--output',
                      help='Output file.')
    fmt = 'Quantity to show.  Possible values:\n{}'
    pvel.add_argument('-q', '--quantity', type=str.lower,
                      choices=[item.lower() for item in FCHT_QTIES],
                      default='mols',
                      help=fmt.format(', '.join([item.lower()
                                                 for item in FCHT_QTIES])))
    pvel.add_argument('datafile',
                      help='Data file.')
    pvel.set_defaults(mode='vel')
    # Vibrationally-resolved electronic spectroscopy
    pgui = psubs.add_parser('gui', aliases=['GUI', 'main'],
                            help='General interface')
    pgui.add_argument('datafile', nargs='?', action='append',
                      help='Data file.')
    pgui.set_defaults(mode='gui')

    return parser.parse_args(args)


def mode_molview(dfile: DataFile) -> tp.NoReturn:
    """Molview Mode.

    Main function managing molecule viewer.

    Parameters
    ----------
    dfile
        `ep.DataFile` object.
    """
    dkeys = {
        'atcrd': build_qlabel('atcrd', 'last'),
        'atnum': build_qlabel('atnum')
    }
    data = dfile.get_data(*dkeys.values())
    atlab = convert_labsymb(True, *data[dkeys['atnum']]['data'])
    atcrd = np.array(data[dkeys['atcrd']]['data'])*PHYSFACT.bohr2ang
    bonds = list_bonds(atlab, atcrd, 1.2)

    app = QtGui.QGuiApplication(sys.argv)
    view = MolWin(1, atlab, atcrd, bonds, True, False)
    view.show()
    sys.exit(app.exec_())


def mode_vibronic(dfile: DataFile,
                  qty: str) -> tp.NoReturn:
    """Molview Mode.

    Main function managing molecule viewer.

    Parameters
    ----------
    dfile
        `ep.DataFile` object.
    qty
        Quantity of interest.
    """
    # For geometries, some data may not be available
    error_noqty = qty != 'mols'
    dkeys = FCHT_QTIES[qty]
    try:
        data = dfile.get_data(*dkeys.values(), error_noqty=error_noqty)
    except IndexError:
        print('Data not available in file.')
        sys.exit()
    except QuantityError:
        print('Quantity not supported. Someone was lazy...')
        sys.exit(1)
    if qty == 'mols':
        if data[dkeys['IniS']] is None:
            print('Data not available in file.')
            sys.exit()
        # Check with geometry to use for the final state
        # If extrapolated geometry available (VH, VG), uses it
        # If intermediate state defined, RR, so use it
        # Otherwise, use standard final state definition.
        if data[dkeys['ExtG']] is not None:
            fs = 'ExtG'
        elif data[dkeys['MidS']] is not None:
            fs = 'MidS'
        else:
            fs = 'FinS'
        if not data[dkeys[fs]].get('data', False):
            print('ERROR: Something went wrong, final-state geom. missing.')
            sys.exit()
        atlabs = []
        atcrds = []
        bonds = []
        molcols = []
        i = 0
        for sta in ('IniS', fs):
            atlabs.append(convert_labsymb(True, *data[dkeys['atnum']]['data']))
            atcrds.append(np.array(data[dkeys[sta]]['data'])*PHYSFACT.bohr2ang)
            bonds.append(list_bonds(atlabs[-1], atcrds[-1], 1.2))
            molcols.append(MOLCOLS[i])
            i += 1
        app = QtGui.QGuiApplication(sys.argv)
        view = MolWin(2, atlabs, atcrds, bonds, True, True, molcols)
        view.show()
        sys.exit(app.exec_())
    else:
        if qty == 'jmat' and not data[dkeys['JMat']]['data']:
            print('J is the identity matrix.')
        else:
            figsize = (10, 8)
            fig, subp = plt.subplots(1, 1)
            fig.set_size_inches(figsize)
            if qty == 'jmat':
                mat = np.array(data[dkeys['JMat']]['data'])
                plot = plot_jmat(mat, subp)
                fig.colorbar(plot)
            elif qty == 'fulljmat':
                mat = np.array(data[dkeys['JFul']]['data'])
                plot = plot_jmat(mat, subp)
                fig.colorbar(plot)
            elif qty == 'cmat':
                mat = np.array(data[dkeys['CMat']]['data'])
                norm, plot = plot_cmat(mat, subp)
                print('Normalization factor: {:15.6e}'.format(norm))
                fig.colorbar(plot)
            elif qty == 'kvec':
                mat = np.array(data[dkeys['KVec']]['data'])
                plot = plot_kvec(mat, subp)
            elif qty == 'spec':
                if 'y1' in data[dkeys['Pars']]:
                    leg = {y: data[dkeys['Pars']][y]
                           for y in data[dkeys['Pars']]
                           if y.startswith('y')}
                else:
                    leg = None
                stick = data[dkeys['Pars']]['func'].lower() == 'stick'
                bounds = plot_spec_2D(data[dkeys['Spec']], subp, legends=leg,
                                      is_stick=stick)
            plt.show()


def mode_vibspec(dfile) -> tp.NoReturn:
    """Vibrational-spectroscopy Mode.

    Main function managing vibratonal spectroscopy.

    Parameters
    ----------
    dfile
        ep.DataFile object
    """
    print('ERROR: Vibrational spectroscopy not yet available.')
    sys.exit(1)


def main() -> tp.NoReturn:
    """Main function.
    """
    args = parse_args(sys.argv[1:])
    if args.mode == 'gui':
        print('ERROR: Not yet available')
        sys.exit(1)
    elif args.mode == 'mol':
        fname = args.datafile
        if not os.path.exists(fname):
            print(f'ERROR: File "{fname}"" not found.')
            sys.exit(2)
        dfile = DataFile(fname)
        mode_molview(dfile)
    elif args.mode == 'vel':
        fname = args.datafile
        if not os.path.exists(fname):
            print(f'ERROR: File "{fname}"" not found.')
            sys.exit(2)
        dfile = DataFile(fname)
        mode_vibronic(dfile, args.quantity)


if __name__ == '__main__':
    main()
