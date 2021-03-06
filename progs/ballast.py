"""
    BALLAST: Builder Assistant to Lay out, Label and Arrange Spectra
                                Together

This is a simple program to combine and display spectra together.
"""

import sys
import os
import re
import argparse
import typing as tp
import configparser as cfg
from math import *

import numpy as np
import matplotlib.pyplot as plt

from estampes.base.spectrum import Spectrum
from estampes.tools.char import convert_expr
from estampes.visual.plotspec import SpecLayout


def fscale(expr: str, var: str) -> tp.Callable[[float], float]:
    """Returns a scaling function.

    Analyzes the mathematical expression in `expr` and returns a
      function compatible with `var`.

    Parameters
    ----------
    expr
        Mathematical expression.
    var
        Variable of interest.

    Returns
    -------
    function
        Mathematical function

    Raises
    ------
    NameError
        Unsupported mathematical function.
    """
    try:
        _expr = convert_expr(expr, var, natural=True)
    except ValueError:
        return NameError('Wrong mathematical functions detected.')

    return eval('lambda {}: {}'.format(var, _expr))


def build_opts(parser: argparse.ArgumentParser) -> tp.NoReturn:
    """Builds commandline options.

    Builds commandline options inside input `parser`.

    Parameters
    ----------
    parser
        Parser to update.
    """
    parser.add_argument('optfile', nargs='?',
                        help='Option file (INI style).')
    # parser.add_argument('-o', '--output',
    #                   help='Output file.')
    parser.add_argument('-c', '--colors', action='append',
                        help='Spectral colors.')
    msg = '''\
Colors of the spectra.  By default, it follows the order of input files.
It is possible to change the order by putting a number followed by ":".
Ex. '3:Test' means that the label 'Test' is for the 3rd file (start at 1).
'r'/'e'/'0' refers to the reference data.
'''
    parser.add_argument('-i', '--inpfile', action='append',
                        help='Input data file.')
    msg = '''\
Labels for the legend.  By default, it follows the order of input files.
It is possible to change the order by putting a number followed by ":".
Ex. '3:Test' means that the label 'Test' is for the 3rd file (start at 1).
'r'/'e'/'0' refers to the reference data.
'''
    parser.add_argument('-l', '--label', action='append',
                        help=msg)
    parser.add_argument('-r', '--refdata',
                        help='Reference spectrum file.')


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
    build_opts(parser)
    return parser.parse_args(args)


def parse_subid(ident: str, ncols: int = 1
                ) -> tp.Tuple[tp.Union[int, tp.Tuple[int, int]],
                              tp.Union[int, tp.Tuple[int, int]]]:
    """Parses a subplot identifier.

    Takes a subplot identifier and returns the corresponding row and
      column.

    Parameters
    ----------
    ident
        Identifier string.
    ncols
        Number of columns.

    Returns
    -------
    int, tuple
        Row index (starting from 1) or interval as tuple of integers.
    int, tuple
        Column index (starting from 1) or interval as tuple of integers.

    Raises
    ------
    ValueError
        Unable to parse the subplot specification.
    """
    def split_coord(coord: str) -> tp.Union[int, tp.Tuple[int, int]]:
        """Splits correctly a single coordinate."""
        if not coord.strip():
            return 1
        else:
            res = coord.split('-')
            if len(res) == 2:
                if not res[0].strip():
                    i = 1
                else:
                    i = max(int(res[0]), 1)
                if not res[1].strip():
                    j = -1
                else:
                    j = max(int(res[1]), 1)
                if i == j:
                    return i
                else:
                    return (i, j)
            else:
                return max(int(res[0]), 1)

    grid = ident.split(',')
    if len(grid) == 2:
        row = split_coord(grid[0])
        col = split_coord(grid[1])
    elif len(grid) == 1:
        i = int(grid)
        row = max(int(ceil(i/ncols)), 1)
        col = max(i - (row-1)*ncols, 1)
    else:
        raise ValueError('Incorrect subplot specification.')

    return row, col


def parse_inifile(fname: str
                  ) -> tp.Tuple[tp.Dict[str, tp.Any],
                                tp.List[tp.List[SpecLayout]],
                                tp.Dict[str, tp.Any]]:
    """Parses INI file.

    Parses a INI configuration file.

    Parameters
    ----------
    fname
        Filename.

    Returns
    -------
    dict
        Figure data.
    list
        List of lists of spectrum layout parameters (grif format).
    dict
        Curves data.

    Raises
    ------
    FileNotFoundError
        INI file or input file missing.
    ValueError
        Incorrect parameter.
    """
    if not os.path.exists(fname):
        raise FileNotFoundError('Missing INI file')
    opts = cfg.ConfigParser()
    opts.read(fname)
    secs = {key.strip().lower(): key for key in opts.sections()}

    figdat = {
        'title': None,
        'geom': None,
        'shareaxes': False,
        'subp': (1, 1),
        'fname': None,
        'show': True
    }
    if 'figure' in secs:
        optsec = opts[secs['figure']]
        figdat['title'] = optsec.get('maintitle', fallback=None)
        figdat['fname'] = optsec.get('imagefile', fallback=None)
        figdat['show'] = optsec.getboolean('showfigure', fallback=True)
        res = optsec.get('mergeaxes', fallback='None').lower()
        if res == 'none':
            val = False
        elif res == 'x':
            val = 'X'
        elif res == 'y':
            val = 'Y'
        elif res == 'all':
            val = True
        else:
            raise ValueError('Unrecognized value for MergeAxes')
        figdat['shareaxes'] = val
        optkey = optsec.get('subplots', None)
        if optkey is not None:
            res = optkey.replace('(', '').replace(')', '').split(',')
            if len(res) == 1:
                val = (max(int(res[0]), 1), 1)
            else:
                val = (max(int(res[0]), 1), max(int(res[1]), 1))
        else:
            val = None
    else:
        val = None
    if val is not None:
        figdat['subp'] = val
    # nrows and ncols are needed for the subplot specifications,
    # they must not be changed
    nrows, ncols = figdat['subp']
    figdat['nums'] = nrows * ncols
    # Check geometry now since it may be proportional to number of rows/cols
    if 'figure' in secs:
        optkey = optsec.get('geometry', None)
        if optkey is not None:
            res = optkey.replace('(', '').replace(')', '').split(',')
            if len(res) == 1:
                raise ValueError('Incorrect value for geometry.')
            if '*' in res[0] or 'x' in res[0]:
                val1 = float(res[0].replace('x', '').replace('*', ''))*ncols
            else:
                val1 = float(res[0])
            if '*' in res[1] or 'x' in res[1]:
                val2 = float(res[1].replace('x', '').replace('*', ''))*nrows
            else:
                val2 = float(res[1])
            val = (val1, val2)
            figdat['geom'] = val
    spcdat = []
    for _ in range(nrows):
        spcdat.append([None for j in range(ncols)])

    # The layout system works in a slightly different way than curves
    # Besides using defaults, users can use the generic [layout] to define
    #   a common layout.
    # We first build some default setup, which will be used for all others.
    # The keys correspond to SpecLayout
    spckeys = {
        'title': ('title', ),
        'xleft': ('xleft', 'xmin'),
        'xright': ('xright', 'xmax'),
        'ytop': ('ytop', 'ymax'),
        'ybottom': ('ybottom', 'ymin'),
        'xscale': ('xscale', ),
        'yscale': ('yscale', ),
        'xlabel': ('xlabel', ),
        'ylabel': ('ylabel', ),
        'legpos': ('legend', ),
        'legcol': ('legend_cols', ),
        'plottag': ('panel', )
    }
    spcbase = {
        'title': None,
        'xleft': None,
        'xright': None,
        'ytop': None,
        'ybottom': None,
        'xscale': 'linear',
        'yscale': 'linear',
        'xlabel': None,
        'ylabel': None,
        'legpos': 'best',
        'legcol': 1,
        'plottag': None
    }
    if 'layout' in secs:
        optsec = opts[secs['layout']]
        for key in spckeys:
            for alias in spckeys[key]:
                if alias in optsec:
                    spcbase[key] = optsec[alias]
                    break

    for sec in secs:
        if sec.startswith('layout'):
            res = sec.split(':')
            if len(res) == 2:  # Ignore default case here
                row, col = parse_subid(res[1], ncols)
                if isinstance(row, tuple) or isinstance(col, tuple):
                    msg = 'Subplot ranges not supported in layout specs.'
                    raise ValueError(msg)
                if row > nrows or col > ncols:
                    break
                # correct to Python indexes
                row -= 1
                col -= 1
                optsec = opts[secs[sec]]
                val = {}
                for key in spckeys:
                    for alias in spckeys[key]:
                        if alias in optsec:
                            val[key] = optsec[alias]
                            break
                    else:
                        val[key] = spcbase[key]
                spcdat[row][col] = SpecLayout(**val)
    for row in range(nrows):
        for col in range(ncols):
            if spcdat[row][col] is None:
                spcdat[row][col] = SpecLayout(**spcbase)
    # If axes merged, removed unnecessary labels
    if figdat['shareaxes'] in ('Y', True) and ncols > 1:
        for i in range(nrows):
            for j in range(1, ncols):
                spcdat[i][j].ylabel = None
    if figdat['shareaxes'] in ('X', True) and nrows > 1:
        for i in range(nrows-1):
            for j in range(ncols):
                spcdat[i][j].xlabel = None

    curves = {}
    for sec in secs:
        if sec.startswith('curve'):
            res = sec.split(':', maxsplit=1)
            if res[0] != 'curve':
                print(sec, 'will be ignored as a curve definition.')
                continue  # This is not a right keyword, ignore.
            if len(res) != 2:
                key = ' '
            else:
                key = secs[sec].split(':', maxsplit=1)[1].strip()
            optsec = opts[secs[sec]]
            # Check if curve to be shown
            if not optsec.getboolean('show', fallback=True):
                continue
            # Subplot - check if subplot within range
            res = optsec.get('subplot', fallback=None)
            if res is not None:
                val1 = parse_subid(res, ncols)
                val = [[None, None], [None, None]]
                for i, item in enumerate(val1):
                    if isinstance(item, int):
                        val[i] = (item-1, item-1)
                    else:
                        val[i][0] = item[0] - 1
                        if item[1] == -1:
                            if i == 0:
                                val[i][1] = nrows - 1
                            else:
                                val[i][1] = ncols - 1
                        else:
                            val[i][1] = item[1] - 1
                row, col = val
                if row[-1] >= nrows or col[-1] >= ncols:
                    continue
                curves[key] = {'subplot': (row, col)}
            else:
                curves[key] = {'subplot': ((0, nrows-1), (0, ncols-1))}
            if 'file' not in optsec:
                print(f'WARNING: Missing file for "{sec}". Ignoring.')
                continue
            elif not os.path.exists(optsec['file']):
                fmt = 'ERROR: File "{}" not found in "{}".'
                print(fmt.format(optsec['file'], sec))
            spc = optsec.get('spectroscopy', fallback=None)
            lvl = optsec.get('level', fallback=None)
            if spc is None or lvl is None:
                raise ValueError('Spectroscopy not defined')
            yid = optsec.get('yaxis', None)
            if yid is not None:
                yid = 'y' + yid
            curves[key]['data'] = Spectrum(optsec['file'], spc, lvl, yid)
            if optsec.getboolean('broaden', fallback=False):
                func = optsec.get('function', None)
                hwhm = optsec.getfloat('hwhm', fallback=10.)
                xmin = optsec.getfloat('newxmin', fallback=None)
                xmax = optsec.getfloat('newxmax', fallback=None)
                xres = optsec.getfloat('grain', fallback=4.)
                curves[key]['data'].set_broadening(hwhm, func, 'default', xres,
                                                   xmin, xmax)
            vizdata = {}
            for item in ('color', 'linestyle', 'linewidth'):
                if optsec.get(item, False):
                    vizdata[item] = optsec.get(item, False)
            if vizdata:
                curves[key]['data'].set_display(**vizdata)
            if optsec.get('label', None) is not None:
                curves[key]['data'].label = optsec.get('label')
            curves[key]['xshift'] = optsec.getfloat('xshift', fallback=None)
            res = optsec.get('xscale', None)
            if res is not None:
                data = res.split(',')
                try:
                    curves[key]['xscale'] = fscale(data[-1], 'x')
                except NameError:
                    msg = 'Incorrect scaling factor for X'
                    raise ValueError(msg) from None
                if len(data) > 1:
                    val = data[0].lower()
                    if val in ('rel', 'relative'):
                        curves[key]['xrelscale'] = True
                    elif val in ('abs', 'absolute'):
                        curves[key]['xrelscale'] = False
                    else:
                        msg = 'Incorrect scaling method for X'
                        raise ValueError(msg)
                else:
                    curves[key]['xrelscale'] = False
            else:
                curves[key]['xscale'] = None
            res = optsec.get('yshift', None)
            if res is not None:
                try:
                    val = float(res)
                except ValueError:
                    if res.lower() in ('base', 'baseline'):
                        val = 'base'
                    else:
                        msg = 'Unsupported value for YShift'
                        raise ValueError(msg) from None
            else:
                val = None
            curves[key]['yshift'] = val
            res = optsec.get('yscale', None)
            if res is not None:
                data = res.split(',')
                try:
                    curves[key]['yscale'] = fscale(data[-1], 'y')
                except NameError:
                    msg = 'Incorrect scaling factor for Y'
                    raise ValueError(msg) from None
                if len(data) > 1:
                    val = data[0].lower()
                    if val in ('rel', 'relative'):
                        curves[key]['yrelscale'] = True
                    elif val in ('abs', 'absolute'):
                        curves[key]['yrelscale'] = False
                    else:
                        msg = 'Incorrect scaling method for Y'
                        raise ValueError(msg)
                else:
                    curves[key]['yrelscale'] = True
            else:
                curves[key]['yscale'] = None
            curves[key]['ynorm'] = optsec.getboolean('normalize',
                                                     fallback=False)
            if 'outputfile' in optsec:
                curves[key]['outfile'] = \
                    optsec.get('outputfile').format(curve=key)

    return figdat, spcdat, curves


def main() -> tp.NoReturn:
    """Main function.
    """
    args = parse_args(sys.argv[1:])
    if not args.inpfile and not args.optfile:
        print('ERROR: Missing files or option file.')
        sys.exit(2)
    elif args.inpfile and args.optfile:
        msg = 'ERROR: Option file and single files cannot be treated' \
            + ' together'
        print(msg)
        sys.exit(2)
    elif args.inpfile:
        print('ERROR: Files in input not yet supported')
    else:
        figdata, spcdata, curves = parse_inifile(args.optfile)

    nrows, ncols = figdata['subp']
    y0lines = np.full((nrows, ncols), False)
    pars = {'tight_layout': True}
    res = figdata['shareaxes']
    if res == 'X' or res is True:
        pars['sharex'] = True
        if 'gridspec_kw' not in pars:
            pars['gridspec_kw'] = {}
        pars['gridspec_kw']['hspace'] = 0.0
    if res == 'Y' or res is True:
        pars['sharey'] = True
        if 'gridspec_kw' not in pars:
            pars['gridspec_kw'] = {}
        pars['gridspec_kw']['wspace'] = 0.0
    fig, subp = plt.subplots(nrows, ncols, **pars)
    if figdata['geom'] is not None:
        fig.set_size_inches(figdata['geom'])
    # Build the curves, one at a time and then include in all relevant
    #   plot to avoid multiple iterations of heavy operations like broaden.
    for idcurve, key in enumerate(curves):
        xaxis = np.array(curves[key]['data'].xaxis)
        if curves[key]['xscale'] is not None:
            if curves[key]['xrelscale']:
                shift = min(xaxis, key=lambda x: abs(x))
                xaxis -= shift
            func = np.vectorize(curves[key]['xscale'])
            xaxis = func(xaxis)
            if curves[key]['xrelscale']:
                xaxis += func(shift)
        if curves[key]['xshift'] is not None:
            xaxis += curves[key]['xshift']
        yaxis = np.array(curves[key]['data'].yaxis)
        ymin = np.min(yaxis)
        ymax = np.max(yaxis)
        add_y0 = ymin*ymax < 0 and (
            min(abs(ymin), ymax)/max(abs(ymin), ymax) > .1)
        if curves[key]['yscale'] is not None:
            if curves[key]['yrelscale']:
                shift = min(yaxis, key=lambda x: abs(x))
                yaxis -= shift
            func = np.vectorize(curves[key]['yscale'])
            yaxis = func(yaxis)
            if curves[key]['yrelscale']:
                yaxis += func(shift)
        if curves[key]['ynorm']:
            yshift = min(yaxis, key=lambda x: abs(x))
            yaxis -= yshift
            ymax = np.max(np.abs(yaxis))
            yaxis /= ymax
            yaxis += yshift/ymax
        if curves[key]['yshift'] is not None:
            if curves[key]['yshift'] == 'base':
                if ymin*ymax >= 0:
                    if ymin >= 0:
                        yshift = - ymin
                    else:
                        yshift = + ymax
                else:
                    yshift = 0
            else:
                yshift = curves[key]['yshift']
            yaxis += yshift
        stick = curves[key]['data'].get_broadening('func') == 'stick'
        if 'outfile' in curves[key]:
            fmt = '{:12.5f}, {:15.6e}\n'
            with open(curves[key]['outfile'], 'w') as fobj:
                for i in range(len(xaxis)):
                    fobj.write(fmt.format(xaxis[i], yaxis[i]))
        data = {}
        if curves[key]['data'].label is not None:
            data['label'] = curves[key]['data'].label
        if curves[key]['data'].linecolor is not None:
            data['color'] = curves[key]['data'].linecolor
        elif stick:
            # stick is done with vertical lines, always black by default
            # For this reason, we set a color.  Otherwise, let the normal
            #   plotting tools select automatically.
            data['color'] = 'C{:d}'.format(idcurve)
        if curves[key]['data'].linewidth is not None:
            data['linewidth'] = curves[key]['data'].linewidth
        if not stick and curves[key]['data'].linestyle is not None:
            data['linestyle'] = curves[key]['data'].linestyle
        irow, icol = curves[key]['subplot']
        for row in range(irow[0], min(irow[1]+1, nrows)):
            for col in range(icol[0], min(icol[1]+1, ncols)):
                y0lines[row, col] = y0lines[row, col] or add_y0
                if nrows > 1 and ncols > 1:
                    sub = subp[row, col]
                elif nrows > 1:
                    sub = subp[row]
                elif ncols > 1:
                    sub = subp[col]
                else:
                    sub = subp
                if stick:
                    zeros = np.zeros(len(yaxis))
                    sub.vlines(xaxis, zeros, yaxis, **data)
                else:
                    sub.plot(xaxis, yaxis, **data)
    # Now set the plot grid.
    for row in range(nrows):
        for col in range(ncols):
            if nrows > 1 and ncols > 1:
                sub = subp[row, col]
            elif nrows > 1:
                sub = subp[row]
            elif ncols > 1:
                sub = subp[col]
            else:
                sub = subp
            sub.legend()
            if y0lines[row, col]:
                sub.axhline(0, c='.5', zorder=-10.0)
            spcdata[row][col].set_plot(sub)
    if figdata['title'] is not None:
        fig.suptitle(figdata['title'], fontweight='bold')
    if figdata['fname'] is not None:
        plt.savefig(figdata['fname'], bbox_inches='tight')
    if figdata['show']:
        plt.show()


if __name__ == '__main__':
    main()
