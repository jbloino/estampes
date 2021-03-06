"""Low-level operations on Gaussian formatted checkpoint files.

Provides low-level interfaces to manipulate/extract data in Gaussian
  formatted checkpoint files.

Attributes
----------

Methods
-------
get_data
    Gets data from a FChk file for each quantity label.
get_hess_data
    Gets or Builds Hessian data (eigenvectors and values).

Classes
-------
FChkIO
    Main class to handle formatted checkpoint file operations.

Notes
-----
* While this module contains some basic error checks, it is intended to
  be low-level, with no significant performance impacts.  As a low-level
  module, it should not be accessible to users but wrapped by developers
  in charge of controlling that the queries are computationally AND
  physically sound.

"""

import os  # Used for file existence check
import re  # Used to find keys in fchk file
from tempfile import TemporaryFile
from shutil import copyfileobj
import typing as tp
from math import ceil

from estampes import parser as ep
from estampes.base import ArgumentError, ParseDataError, ParseKeyError, \
    QuantityError, TypeData, TypeDCrd, TypeDFChk, TypeDOrd, TypeQInfo, \
    TypeQLvl, TypeQOpt, TypeQTag, TypeRSta
from estampes.data import property as edpr

# ================
# Module Constants
# ================

TypeQData = tp.Dict[str, tp.Optional[tp.Any]]
TypeKword = tp.Dict[str, tp.Tuple[str, int, int]]
TypeQKwrd = tp.Union[str, tp.List[str]]

NCOLS_FCHK = {  # Maximum number of columns per type in fchk
    'C': 5,  # Number of columns for character data per line
    'R': 5,  # Number of columns for float data per line
    'I': 6  # Number of columns for integer data per line
}
FCONV_FCHK = {  # Conversion function for each type
    'C': str,
    'I': int,
    'R': float
}
DFMT_FCHK = {  # Data format for each type
    'C': '{:12s}',
    'I': '{:12d}',
    'R': '{:16.8E}'
}

# ==============
# Module Classes
# ==============


class FChkIO(object):
    """Main class to handle formatted checkpoint file operations.

    Main class to manage the parsing and formatting of data stored in
      Gaussian formatted checkpoint file.

    Attributes
    ----------
    filename : str
        Formatted checkpoint filename
    version : str
        Version, software-dependent
    full_version : tuple
        full version:
        * Gaussian
        * Gaussian major and minor revisions, mach and relesase date

    Methods
    -------
    read_data(to_find, raise_error)
        Extracts 1 or more data blocks from the fchk file
    write_data(data, new_file, error_key, error_size)
        Writes data corresponding to the keys to find.
    show_keys()
        Shows available keys in fchk if loaded
    """
    def __init__(self, fname: str,
                 load_keys: bool = True) -> None:
        self.filename = fname
        if load_keys:
            self.__keys = self.__store_keys()  # type: tp.Optional[TypeKword]
        else:
            self.__keys = None
        try:
            key = 'Gaussian Version'
            qtag = 'swver'
            qdict = {qtag: list(ep.parse_qlabel(qtag))}
            qkwrd = {qtag: key}
            qdata = self.read_data(key)
            self.__gversion = parse_data(qdict, qkwrd, qdata)[qtag]
        except ParseKeyError:
            self.__gversion = {'major': None, 'minor': None}

    @property
    def filename(self) -> str:
        """Gets or sets the filename associated to the FChk object."""
        return self.__fname

    @filename.setter
    def filename(self, name: str) -> None:
        if not os.path.exists(name):
            raise FileNotFoundError('Formatted checkpoint not found')
        self.__fname = name

    @property
    def version(self) -> tp.Dict[str, str]:
        """Returns the version of Gaussian used to generate the FChk.

        Notes
        -----
        Earlier versions of Gaussian did not support this so this may be
          empty.
        """
        return self.__gversion

    def show_keys(self):
        """Returns the available keys (only if loaded)."""
        if self.__keys is None:
            return None
        else:
            return sorted(self.__keys.keys())

    @property
    def full_version(self) -> tp.Tuple[str, tp.Any]:
        """Returns the full version, for the parser interface"""
        return "Gaussian", self.__gversion

    def read_data(self,
                  *to_find: tp.Tuple[str],
                  raise_error: bool = True) -> TypeQData:
        """Extracts data corresponding to the keys to find.

        Parameters
        ----------
        to_find
            Key or list of keys to find.
        raise_error
            Only raises error if True, otherwise proceeds silently.

        Raises
        ------
        ParseKeyError
            Key not found.
        """

        keylist = []  # List of keywords to search
        datlist = {}  # type: TypeQData # List of data

        # Fast Search
        # -----------
        # Uses the data in __keys to find pointers.
        if self.__keys is not None:
            # Build keyword list
            # ^^^^^^^^^^^^^^^^^^
            for item in to_find:
                if not item.strip() in self.__keys:
                    if raise_error:
                        raise ParseKeyError(item)
                else:
                    keylist.append([item, *self.__keys[item]])
            # Sort the keys by order of appearance
            keylist.sort(key=lambda x: x[3])
            with open(self.filename, 'r') as fobj:
                for item in keylist:
                    key, dtype, ndata, fpos = item
                    fobj.seek(fpos)
                    line = fobj.readline()
                    datlist[key] = self.__read_datablock(fobj, line, dtype,
                                                         ndata)

        # Sequential Search
        # -----------------
        # Looks for keywords sequentially while reading file
        else:
            nkeys = len(to_find)
            with open(self.filename, 'r') as fobj:
                line = fobj.readline()
                while line and nkeys > 0:
                    line = fobj.readline()
                    for key in to_find:
                        if line.startswith(key):
                            datlist[key] = self.__read_datablock(fobj, line)
                            nkeys -= 1
            remaining = list(set(to_find) - set(datlist))
            if len(remaining) > 0 and raise_error:
                raise ParseKeyError(remaining[0])

        return datlist

    def write_data(self,
                   data: tp.Dict[str, tp.Sequence[tp.Any]],
                   new_file: tp.Optional[str] = None,
                   error_key: bool = True,
                   error_size: bool = True) -> None:
        """Writes data corresponding to the keys to find.

        Reads a dictionary of keys and overwrites the data present in
          the file.
        If the key is not present or the size is inconsistent with the
          data present in the file, an error is raised, except if
          `error_key` or `error_size` are False, respectively.

        Parameters
        ----------
        data
            Dictionary with the replacement data for each key.
        new_file
            Name of the file where data are printed.
            If none, the internal file is overwritten.
        error_key
            If true, raises error if key not found.
        error_size
            If true, raises error for inconsistent size.

        Raises
        ------
        ParseKeyError
            Key not found.
        IndexError
            Inconsistency in size between old and new data for a key.
        """
        fmt_scal = {
            'I': '{:<40s}   I     {:12d}\n',
            'R': '{:<40s}   R     {:22.15E}\n',
            'C': '{:<40s}   C     {:12s}\n',
        }
        fmt_head = '{:<40s}   {:1s}   N={:12d}\n'

        # Compared available keys with those from new data set
        # ----------------------------------------------------
        keys_ok = {}
        if self.__keys is not None:
            # Uses the data in __keys to find pointers.
            # Check if overlap between data and stored keys
            keys = set(self.__keys) & set(data)
            for key in keys:
                keys_ok[self.__keys[key][-1]] = key
            keys_no = list(set(data) - keys)
        else:
            nkeys = len(data)
            keys_no = data.keys()
            with open(self.filename, 'r') as fobj:
                line = fobj.readline()
                while line and nkeys > 0:
                    fpos = 0
                    if line[0] != ' ':
                        for index, key in enumerate(keys_no):
                            if line.startswith(key):
                                keys_ok[fpos] = keys_no.pop(index)
                                nkeys -= 1
                    fpos += len(line)
                    line = fobj.readline()
        if keys_no and error_key:
            raise ParseKeyError(', '.join(keys_no))

        # Now set where data are to be saved
        if new_file is None:
            fdest = TemporaryFile()
        else:
            fdest = open(new_file, 'w')

        # Now let us copy the content of the internal file in destination
        # For each key retained after the analysis, we replace with the new
        # data
        with open(self.filename, 'r') as fsrc:
            fpos = 0
            for line in fsrc:
                if fpos in keys_ok:
                    key = keys_ok[fpos]
                    dtype, ndat_ref, ncols, nlin_ref = self.__info_block(line)
                    ndat_new = len(data[key])
                    if ndat_ref == 0:
                        if ndat_new > 1 and error_size:
                            raise IndexError(f'Inconsistency with {key}')
                        else:
                            fdest.write(fmt_scal[dtype].format(key, data[key]))
                    else:
                        fdest.write(fmt_head.format(key, ndat_new))
                        for i in range(0, ndat_new, ncols):
                            N = min(ncols, ndat_new-i)
                            fmt = N*DFMT_FCHK[dtype] + '\n'
                            fdest.write(fmt.format(data[key][i:i+N]))
                        for _ in range(nlin_ref):
                            line = next(fsrc)
                            fpos += len(line)
                else:
                    fdest.write(line)
                    fpos += len(line)

            # Copy back file if requested
            if new_file is not None:
                fdest.seek(0)
                fsrc.seek(0)
                copyfileobj(fdest, fsrc)

    def __store_keys(self) -> TypeKword:
        """Stores the keys in the fchk to speed up search.

        Loads the keys present in the file and pointers to their
          position to speed up their search.
        Data type and block information are also stored.

        Returns
        -------
        dict
            For each key, returns a tuple with:
            1. data type (I, R, C)
            2. Number of values (0 for scalar)
            3. position in files
        """
        to_search = re.compile(r'''
            (?P<title>[\w\s/\-]+?)\s*  # Key
            \b(?P<type>[IRC])\b\s*  # Data type
            (?P<block>N=)?\s+  # N= only set for non-scalar data
            (?P<value>[\d\-\+\.E]+)  # Block size (N=) or scalar value
            $''', re.VERBOSE)

        keys = {}
        with open(self.filename, 'r') as fobj:
            fpos = 0
            for line in fobj:
                res = to_search.match(line)
                if res:
                    nval = int(res.group(3) and res.group(4) or 0)
                    keys[res.group(1)] = (res.group(2), nval, fpos)
                fpos += len(line)
        return keys

    def __info_block(self, line: tp.Optional[str] = None,
                     datatype: tp.Optional[str] = None,
                     numdata: tp.Optional[int] = None
                     ) -> tp.List[tp.Any]:
        """Extracts information on a given block.

        Extracts information on a block, either from the line or data
          in arguments.

        Parameters
        ----------
        line
            Starting line of a block.
        datatype
            Type of data.
        numdata
            Number of data.

        Returns
        -------
        str
            Type of data.
        int
            Number of data.
        int
            Number of columns.
        int
            Number of lines.

        Raises
        ------
        ArgumentError
            Arguments are insufficient to generate the data.
        ParseDataError
            Unsupported data types.
        """
        if datatype is None and line is None:
            raise ArgumentError('line and datatype cannot be both absent')
        # If data type unknown, line has not been parsed
        if datatype is None:
            cols = line.split()
            if 'N=' in line:
                dtype = cols[-3]
                ndata = int(cols[-1])
            else:
                dtype = cols[-2]
                ndata = 0
        else:
            dtype = datatype
            ndata = numdata
        # Sets parameters:
        try:
            ncols = NCOLS_FCHK[dtype]
        except KeyError:
            raise ParseDataError(dtype, 'Unsupported data type')
        nlines = int(ceil(ndata/ncols))
        return dtype, ndata, ncols, nlines

    def __read_datablock(self, fobj: tp.TextIO,
                         line: str,
                         datatype: tp.Optional[str] = None,
                         numdata: tp.Optional[int] = None
                         ) -> tp.List[tp.Any]:
        """Reads a data block in the formatted checkpoint file.

        Reads a data block from a Gaussian formatted checkpoint file.
        The file "cursor" should be at the "title/section line" and the
          content stored in 'line'.

        Parameters
        ----------
        fobj
            Opened file.
        line
            Current line read from file object.
        datatype
            Type of the scalar or data block.
        numdata
            Size of the data block (0 if scalar).

        Raises
        ------
        ParseDataError
            Unsupported data type.

        Notes
        -----
        * The function uses readline() to extract the actual block.
        * The parsing is mostly format-free for simplicity.

        .. [1] http://gaussian.com/interfacing/?tabid=3
        """
        dtype, _, _, nlines = self.__info_block(line, datatype, numdata)
        # Sets parameters:
        try:
            fconv = FCONV_FCHK[dtype]
        except KeyError:
            raise ParseDataError(dtype, 'Unsupported data type')
        # Data Extraction
        # ---------------
        if nlines == 0:
            # Scalar
            data = [fconv(line.split()[-1])]
        else:
            # Data Block
            # We use a slightly different scheme for C since Gaussian cuts
            # arbitrarily strings in the middle in the format
            if dtype == 'C':
                block = ''
                for _ in range(nlines):
                    block += fobj.readline().rstrip('\n')  # Remove newline
                data = block.split()
            else:
                data = []
                for _ in range(nlines):
                    line = fobj.readline()
                    data.extend([fconv(item) for item in line.split()])
        return data

# ================
# Module Functions
# ================


def qlab_to_kword(qtag: TypeQTag,
                  qopt: TypeQOpt = None,
                  dord: TypeDOrd = None,
                  dcrd: TypeDCrd = None,
                  rsta: TypeRSta = None,
                  qlvl: TypeQLvl = None) -> TypeQKwrd:
    """Returns the keyword(s) relevant for a given quantity.

    Returns the keyword corresponding to the block containing the
      quantity of interest and the list all keywords of interest for
      possible conversions.

    Parameters
    ----------
    qtag
        Quantity identifier or label.
    qopt
        Quantity-specific options.
    dord
        Derivative order.
    dcrd
        Reference coordinates for the derivatives.
    rsta
        Reference state or transition:
        - scalar: reference state
        - tuple: transition
    qlvl
        Level of theory use to generate the quantity.

    Returns
    -------
    list
        List of keywords for the data to extract.
    list
        Information needed for extracting the quantity of interest.
        1. keyword in the formatted checkpoint file
        2. position of the first element in the data block
        3. offsets for "sub-block" storage (data in several blocks)

    Raises
    ------
    NotImplementedError
        Missing features.
    QuantityError
        Unsupported quantity.
    ValueError
        Unsupported case.

    Notes
    -----
    - `n` refers to all available states.
    """
    keywords = []
    keyword = None
    if qtag == 'natoms':
        keyword = 'Number of atoms'
    elif qtag == 'nvib':
        keyword = 'Number of Normal Modes'
    elif qtag == 'atmas':
        keyword = 'Real atomic weights'
    elif qtag == 'atnum':
        keyword = 'Atomic numbers'
    elif qtag == 'molsym':
        raise NotImplementedError()
    elif qtag == 'atcrd' or qtag == 2:
        keyword = 'Current cartesian coordinates'
    elif qtag in ('hessvec', 'hessval'):
        if qtag == 'hessvec':
            keyword = 'Vib-Modes'
        else:
            keyword = 'Vib-E2'
    elif qtag == 'swopt':
        keyword = 'Route'
    elif qtag == 'swver':
        keyword = 'Gaussian Version'
    elif qtag == 'fcdat':
        raise NotImplementedError()
    elif qtag == 'vptdat':
        raise NotImplementedError()
    elif qtag in ('dipstr', 'rotstr'):
        keywords = ['ETran scalars']
        if isinstance(rsta, int) or rsta == 'c':
            if qopt == 'H':
                keyword = 'Vib-E2'
                keywords.append('Number of Normal Modes')
            else:
                keyword = 'Anharmonic Vib-E2'
                keywords.append('Anharmonic Number of Normal Modes')
        else:
            keyword = 'ETran state values'
    else:
        if isinstance(rsta, tuple):
            keyword = 'ETran state values'
            if qtag == 1 and dord == 0:
                keywords = ['ETran scalars', 'SCF Energy']
        else:
            if qtag == 1:
                if dord == 0:
                    if rsta == 'c':
                        keyword = 'Total Energy'
                        del keywords[:]
                    elif type(rsta) is int:
                        if rsta == 0:
                            keyword = 'SCF Energy'
                        else:
                            keyword = 'ETran state values'
                        keywords.append('Total Energy', 'ETran scalars')
                elif dord == 1:
                    if dcrd is None or dcrd == 'X':
                        if rsta == 'c' or type(rsta) is int:
                            keyword = 'Cartesian Gradient'
                elif dord == 2:
                    if dcrd is None or dcrd == 'X':
                        if rsta == 'c' or type(rsta) is int:
                            keyword = 'Cartesian Force Constants'
            elif qtag == 50:
                raise NotImplementedError()
            elif qtag == 91:
                raise NotImplementedError()
            elif qtag == 92:
                keyword = 'RotTr to input orientation'
            elif qtag == 93:
                keyword = 'RotTr to input orientation'
            elif qtag == 101:
                if dord == 0:
                    if type(rsta) is int or rsta == 'c':
                        keyword = 'Dipole Moment'
                elif dord == 1:
                    if type(rsta) is int or rsta == 'c':
                        keyword = 'Dipole Derivatives'
            elif qtag == 102:
                if dord == 0:
                    if type(rsta) is int or rsta == 'c':
                        raise ParseDataError('Magnetic dipole not available')
                elif dord == 1:
                    if type(rsta) is int or rsta == 'c':
                        keyword = 'AAT'
            elif qtag == 103:
                raise NotImplementedError()
            elif qtag == 104:
                raise NotImplementedError()
            elif qtag == 105:
                raise NotImplementedError()
            elif qtag == 106:
                raise NotImplementedError()
            elif qtag == 107:
                raise NotImplementedError()
            elif qtag == 201:
                raise NotImplementedError()
            elif qtag == 202:
                raise NotImplementedError()
            elif qtag == 203:
                raise NotImplementedError()
            elif qtag == 204:
                raise NotImplementedError()
            elif qtag == 205:
                raise NotImplementedError()
            elif qtag == 206:
                raise NotImplementedError()
            elif qtag == 207:
                raise NotImplementedError()
            elif qtag == 208:
                raise NotImplementedError()
            elif qtag == 209:
                raise NotImplementedError()
            elif qtag == 300:
                if dord == 0:
                    if type(rsta) is int or rsta == 'c':
                        keyword = 'Frequencies for FD properties'
                    else:
                        msg = 'Incident frequencies not available'
                        raise ParseDataError(msg)
                else:
                    keywords = ['Number of atoms']
                    if type(rsta) is int or rsta == 'c':
                        keyword = 'Frequencies for DFD properties'
                    else:
                        msg = 'Incident frequencies not available'
                        raise ParseDataError(msg)
            elif qtag == 301:
                if dord == 0:
                    if type(rsta) is int or rsta == 'c':
                        keyword = 'Alpha(-w,w)'
                elif dord == 1:
                    keywords = ['Number of atoms']
                    if type(rsta) is int or rsta == 'c':
                        keyword = 'Derivative Alpha(-w,w)'
            elif qtag == 302:
                if dord == 0:
                    if type(rsta) is int or rsta == 'c':
                        keyword = 'FD Optical Rotation Tensor'
                elif dord == 1:
                    keywords = ['Number of atoms']
                    if type(rsta) is int or rsta == 'c':
                        keyword = 'Derivative FD Optical Rotation Tensor'
            elif qtag == 303:
                if type(rsta) is int or rsta == 'c':
                    raise ParseDataError('Alpha(w,0) not available')
            elif qtag == 304:
                if dord == 0:
                    if type(rsta) is int or rsta == 'c':
                        keyword = 'D-Q polarizability'
                elif dord == 1:
                    if type(rsta) is int or rsta == 'c':
                        keyword = 'Derivative D-Q polarizability'
            elif qtag == 305:
                if dord == 0:
                    if type(rsta) is int or rsta == 'c':
                        raise NotImplementedError()
                elif dord == 1:
                    keywords = ['Number of atoms']
                    if type(rsta) is int or rsta == 'c':
                        raise NotImplementedError()
            elif qtag == 306:
                if dord == 0:
                    if type(rsta) is int or rsta == 'c':
                        raise NotImplementedError()
                elif dord == 1:
                    keywords = ['Number of atoms']
                    if type(rsta) is int or rsta == 'c':
                        raise NotImplementedError()
            else:
                raise QuantityError('Unknown quantity')
    keywords.insert(0, keyword)
    return keyword, keywords


def _parse_electrans_data(qtag: TypeQTag,
                          dblocks: TypeDFChk,
                          kword: str,
                          qopt: TypeQOpt = None,
                          dord: TypeDOrd = None,
                          dcrd: TypeDCrd = None,
                          rsta: TypeRSta = None
                          ) -> TypeQData:
    """Sub-function to parse electronic-transition related data.

    Parses and returns data for a given quantity related to an
      electronic transition.

    Parameters
    ----------
    qtag
        Quantity identifier or label.
    dblocks
        Data blocks, by keyword.
    kword
        Keyword for quantity of interest.
    qopt
        Quantity-specific options.
    dord
        Derivative order.
    dcrd
        Reference coordinates for the derivatives.
    rsta
        Reference state or transition:
        - scalar: reference state
        - tuple: transition

    Returns
    -------
    dict
        Data for each quantity.

    Raises
    ------
    ParseKeyError
        Missing required quantity in data block.
    IndexError
        State definition inconsistent with available data.
    QuantityError
        Unsupported quantity.
    """
    # ETran Scalar Definition
    # -----------------------
    # Check that ETran scalars are present and parse relevant values
    key = 'ETran scalars'
    if key in dblocks:
        # Structure of ETran scalars
        # 1. Number of electronic states
        # 2. Number of scalar data stored per state
        # 3. 1 of R==L transition matrix, 2 otherwise
        # 4. Number of header words (irrelevant in fchk)
        # 5. State of interest
        # 6. Number of deriv. (3*natoms + 3: electric field derivatives)
        (nstates, ndata, _, _, iroot,
            _) = [item for item in dblocks[key][:6]]
    else:
        raise ParseKeyError('Missing scalars definition')
    # States Information
    # ------------------
    initial, final = rsta
    if initial != 0:
        if final != 0:
            raise IndexError('Unsupported transition')
        else:
            initial, final = final, initial
    # Quantity-specific Treatment
    # ---------------------------
    if qtag == 2:
        key = 'SCF Energy'
        if key not in dblocks:
            raise ParseKeyError('Missing ground-state energy')
        energy0 = dblocks[key]
        if final == 'a':
            data = [dblocks[key][i*ndata]-energy0 for i in range(nstates)]
        else:
            fstate = final == 'c' and iroot or final
            if fstate > nstates:
                raise IndexError('Missing electronic state')
            data = float(dblocks[key][(fstate-1)*ndata]) - energy0
    elif qtag in (101, 102, 103):
        lqty = edpr.property_data(qtag).dim
        if qtag == 101:
            if qopt == 'len':
                offset = 1
            else:
                offset = 4
        elif qtag == 102:
            offset = 7
        else:
            offset = 10
        if dord == 0:
            if final == 'a':
                data = [dblocks[kword][i*ndata+offset:i*ndata+offset+lqty]
                        for i in range(nstates)]
            else:
                fstate = final == 'c' and iroot or final
                if fstate > nstates:
                    raise IndexError('Missing electronic state')
                i0 = (fstate-1)*ndata + offset
                data = dblocks[kword][i0:i0+lqty]
    else:
        raise QuantityError('Unsupported quantity')
    return data


def _parse_freqdep_data(qtag: TypeQTag,
                        dblocks: TypeDFChk,
                        kword: str,
                        qopt: TypeQOpt = None,
                        dord: TypeDOrd = None,
                        dcrd: TypeDCrd = None,
                        rsta: TypeRSta = None
                        ) -> TypeQData:
    """Sub-function to parse data on frequency-dependent properties.

    Parses and returns data on a specific property for one or more
      incident frequencies.

    Parameters
    ----------
    qtag
        Quantity identifier or label.
    dblocks
        Data blocks, by keyword.
    kword
        Keyword for quantity of interest.
    qopt
        Quantity-specific options.
    dord
        Derivative order.
    dcrd
        Reference coordinates for the derivatives.
    rsta
        Reference state or transition:
        - scalar: reference state
        - tuple: transition

    Returns
    -------
    dict
        Data for each quantity.

    Raises
    ------
    ParseKeyError
        Missing required quantity in data block.
    IndexError
        Error with definition of incident frequency.
    QuantityError
        Unsupported quantity.
    """
    # Check Incident Frequency
    # ------------------------
    if qopt is None:
        qopt_ = 0
    elif not isinstance(qopt, int):
        raise IndexError()
    else:
        qopt_ = qopt
    # Quantity-specific Treatment
    # ---------------------------
    # Check size of derivatives is requested
    if dord == 0:
        nder = 1
    elif dord == 1:
        key = 'Number of atoms'
        if key not in dblocks:
            raise ParseKeyError('Missing number of atoms')
        natoms = dblocks[key]
        nder = 3*natoms
    else:
        raise IndexError('Unsupported derivative order')

    if qtag == 301:
        lqty = 9*nder
    elif qtag == 302:
        lqty = 9*nder
    elif qtag == 303:
        lqty = 9*nder
    elif qtag == 304:
        lqty = 18*nder
    elif qtag == 305:
        lqty = 18*nder
    elif qtag == 306:
        lqty = 18*nder
    else:
        raise QuantityError('Unsupported quantity')
    lblock = len(dblocks[kword])
    ndata = lblock // lqty  # Assumed block is correctly built
    if qopt_ == 0:
        data = [dblocks[kword][i*lqty:(i+1)*lqty] for i in range(ndata)]
    else:
        if qopt_ > ndata:
            raise IndexError('Incident frequency index out of range')
        data = dblocks[kword][(qopt_-1)*lqty:qopt_*lqty]

    return data


def parse_data(qdict: TypeQInfo,
               qlab2kword: tp.Dict[str, str],
               datablocks: TypeDFChk,
               gver: tp.Optional[tp.Tuple[str, str]] = None,
               raise_error: bool = True) -> TypeData:
    """Parses data arrays to extract specific quantity.

    Parses data array to extract relevant information for each quantity.

    Parameters
    ----------
    qdict
        Dictionary of quantities.
    qlab2kword
        mMin keyword for each quantity.
    datablocks
        Data blocks, by keyword.
    gver
        Gaussian version (major, minor).
    raise_error
        If True, error is raised if the quantity is not found.

    Returns
    -------
    dict
        Data for each quantity.

    Raises
    ------
    ParseKeyError
        Missing required quantity in data block.
    IndexError
        State definition inconsistent with available data.
    ValueError
        Data inconsistency with respect to shape.
    QuantityError
        Unsupported quantity.
    """
    def empty_cases_ok(qtag, qopt):
        return qtag == 'nvib'
    data = {}
    for qlabel in qdict:
        qtag, qopt, dord, dcrd, rsta, qlvl = qdict[qlabel]
        kword = qlab2kword[qlabel]
        # Basic Check: main property present
        # -----------
        if kword not in datablocks and not empty_cases_ok(qtag, qopt):
            if raise_error:
                raise ParseKeyError('Missing quantity in file')
            else:
                data[qlabel] = None
                continue
        data[qlabel] = {}
        # Basic Properties/Quantities
        # ---------------------------
        if qtag == 'natoms':
            data[qlabel]['data'] = int(datablocks[kword])
        elif qtag in ('atcrd', 2):
            data[qlabel]['data'] = ep.reshape_dblock(datablocks[kword], (3, ))
        elif qtag in ('atmas', 'atnum'):
            data[qlabel]['data'] = datablocks[kword]
        elif qtag == 'swopt':
            data[qlabel]['data'] = ' '.join(datablocks[kword])
        elif qtag == 'molsym':
            raise NotImplementedError()
        elif qtag == 'swver':
            pattern = re.compile(r'(\w+)-(\w{3})Rev([\w.+]+)')
            res = re.match(pattern, ''.join(datablocks[kword])).groups()
            data[qlabel] = {'major': res[1], 'minor': res[2],
                            'system': res[0], 'release': None}
        # Vibrational Information
        # -----------------------
        # Technically state should be checked but considered irrelevant.
        elif qtag == 'nvib':
            if kword in datablocks:
                data[qlabel]['data'] = int(datablocks[kword])
            else:
                # For a robust def of nvib, we need the symmetry and
                #   the number of frozen atoms. For now, difficult to do.
                raise NotImplementedError()
        elif qtag in ('hessvec', 'hessval'):
            if kword in datablocks:
                data[qlabel]['data'] = datablocks[kword]
        # Vibronic Information
        # --------------------
        elif qtag == 'fcdat':
            raise NotImplementedError()
        # Anharmonic Information
        # ----------------------
        elif qtag == 'vptdat':
            raise NotImplementedError()
        # State(s)-dependent quantities
        # -----------------------------
        else:
            # Transition moments
            # ^^^^^^^^^^^^^^^^^^
            if type(rsta) is tuple:
                data[qlabel]['data'] = _parse_electrans_data(qtag, datablocks,
                                                             kword, qopt, dord,
                                                             dcrd, rsta)
            # States-specific Quantities
            # ^^^^^^^^^^^^^^^^^^^^^^^^^^
            else:
                key = 'ETran scalars'
                if key in datablocks:
                    (nstates, ndata, _, _, iroot,
                        _) = [item for item in datablocks[key][:6]]
                curr_sta = rsta == 'c' or rsta == iroot
                # Only energy is currently computed for all states:
                if rsta == 'a' and qtag == 2:
                    data = [float(datablocks[kword][i*ndata])
                            for i in range(nstates)]
                # Data for current electronic states
                elif curr_sta:
                    if qtag in ('dipstr', 'rotstr'):
                        if qlvl == 'H':
                            key = 'Number of Normal Modes'
                        else:
                            key = 'Anharmonic Number of Normal Modes'
                        if key not in datablocks:
                            raise ParseKeyError('Missing necessary dimension')
                        ndat = int(datablocks[key])
                        if qtag == 'dipstr':
                            offset = 7*ndat
                        else:
                            offset = 8*ndat
                        data[qlabel]['data'] = \
                            datablocks[kword][offset:offset+ndat]
                    elif qtag == 1:
                        data[qlabel]['data'] = datablocks[kword]
                    elif qtag == 92:
                        data[qlabel]['data'] = datablocks[kword][:9]
                    elif qtag == 93:
                        data[qlabel]['data'] = datablocks[kword][9:]
                    elif qtag in (50, 91):
                        raise NotImplementedError()
                    elif qtag == 101:
                        if dord in (0, 1):
                            data[qlabel]['data'] = datablocks[kword]
                        else:
                            raise NotImplementedError()
                    elif qtag == 102:
                        if dord == 1:
                            data[qlabel]['data'] = datablocks[kword]
                        else:
                            raise NotImplementedError()
                    elif qtag == 300:
                        if dord in (0, 1):
                            if qopt == 0:
                                data[qlabel]['data'] = datablocks[kword]
                        else:
                            raise NotImplementedError()
                    elif qtag == 300:
                        if dord in (0, 1):
                            data[qlabel]['data'] = datablocks[kword]
                        else:
                            raise NotImplementedError()
                    else:
                        raise NotImplementedError()

    return data


def get_data(dfobj: FChkIO,
             *qlabels: str,
             error_noqty: bool = True) -> TypeData:
    """Gets data from a FChk file for each quantity label.

    Reads one or more full quantity labels from `qlabels` and returns
      the corresponding data.

    Parameters
    ----------
    dfobj
        Formatted checkpoint file as `FChkIO` object.

    *qlabels
        List of full quantity labels to parse.
    error_noqty
        If True, error is raised if the quantity is not found.

    Returns
    -------
    dict
        Data for each quantity.

    Raises
    ------
    TypeError
        Wrong type of data file object.
    ParseKeyError
        Missing required quantity in data block.
    IndexError
        State definition inconsistent with available data.
    QuantityError
        Unsupported quantity.
    """
    # First, check that the file is a correct instance
    if not isinstance(dfobj, FChkIO):
        raise TypeError('FChkIO instance expected')
    # Check if anything to do
    if len(qlabels) == 0:
        return None
    # Build Keyword List
    # ------------------
    # List of keywords
    full_kwlist = []
    main_kwlist = {}
    qty_dict = {}
    for qlabel in qlabels:
        # Label parsing
        # ^^^^^^^^^^^^^
        qty_dict[qlabel] = ep.parse_qlabel(qlabel)
        keyword, keywords = qlab_to_kword(*qty_dict[qlabel])
        if keyword is not None:
            full_kwlist.extend(keywords)
            main_kwlist[qlabel] = keyword
    # Check if list in the end is not empty
    if not main_kwlist:
        raise QuantityError('Unsupported quantities')
    # Data Extraction
    # ---------------
    # Use of set to remove redundant keywords
    datablocks = dfobj.read_data(*list(set(full_kwlist)), raise_error=False)
    # Data Parsing
    # ------------
    gver = (dfobj.version['major'], dfobj.version['minor'])
    try:
        data = parse_data(qty_dict, main_kwlist, datablocks, gver, error_noqty)
    except (QuantityError, NotImplementedError):
        raise QuantityError('Unsupported quantities')
    except (ParseKeyError, IndexError):
        raise IndexError('Missing data in FChk')

    return data


def get_hess_data(natoms: int,
                  get_evec: bool = True,
                  get_eval: bool = True,
                  mweigh: bool = True,
                  dfobj: tp.Optional[FChkIO] = None,
                  hessvec: tp.Optional[tp.List[float]] = None,
                  hessval: tp.Optional[tp.List[float]] = None,
                  atmass: tp.Optional[tp.List[float]] = None,
                  fccart: tp.Optional[tp.List[float]] = None
                  ) -> tp.Tuple[tp.Any]:
    """Gets or builds Hessian data (eigenvectors and values).

    This function retrieves or builds the eigenvectors and eigenvalues.
    Contrary to ``get_data`` which only looks for available data, this
      functions looks for alternative forms to build necessary data.
    It also returns a Numpy array instead of Python lists.

    Parameters
    ----------
    natoms
        Number of atoms.
    get_evec
        Return the eigenvectors.
    get_eval
        Return the eigenvalues.
    mweigh
        Mass-weight the eigenvectors (L = L/M^(-1/2)) for conversions.
    dfobj
        Formatted checkpoint file as `FChkIO` object.
    hessvec
        List containing the eigenvectors of the Hessian matrix.
    hessval
        List containing the eigenvalues of the Hessian matrix (in cm^-1).
    atmass
        List containing the atomic masses.
    fccart
        List containing the Cartesian force constants matrix.

    Returns
    -------
    :obj:numpy.ndarray
        Eigenvectors (None if not requested).
    :obj:numpy.ndarray
        Eigenvalues (None if not requested).

    Raises
    ------
    ValueError
        Inconsitent values given in input.
    IOError
        Error if file object not set but needed.
    IndexError
        Quantity not found.

    Notes
    -----
    * Numpy is needed to run this function
    * Data can be given in argument or will be extracted from `dfobj`
    """
    import numpy as np

    read_data = []
    if not (get_evec or get_eval):
        raise ValueError('Nothing to do')

    if natoms <= 1:
        raise ValueError('Number of atoms must be possitive')

    # Check available data and retrieve what may be needed
    if get_evec:
        if hessvec is None and fccart is None:
            read_data.append('hessvec')
        # if (not mweigh) and atmass is None:
        #     read_data.append('atmas')
        if atmass is None:
            read_data.append('atmas')
    if get_eval:
        if hessval is None and fccart is None:
            read_data.append('hessval')
    if len(read_data) > 0:
        if dfobj is None:
            raise IOError('Missing checkpoint file to extract necessary data')
        idx = read_data.index('hessvec')
        dfdata = {}
        if idx >= 0:
            try:
                dfdata.update(get_data(dfobj, 'hessvec'))
                read_data.pop(idx)
            except IndexError:
                read_data[idx] = ep.build_qlabel(1, None, 2, 'X')
                if 'atmas' not in read_data:
                    read_data.append('atmas')
        dfdata.update(get_data(dfobj, *read_data))

    eigvec, eigval = None, None
    if get_evec:
        if atmass is None:
            atmas = np.repeat(np.array(dfdata['atmas']), 3)
        else:
            atmas = np.repeat(np.array(atmass), 3)
        if hessvec is not None or 'hessvec' in dfdata:
            if hessvec is not None:
                eigvec = np.array(hessvec).reshape((3*natoms, -1), order='F')
            else:
                eigvec = np.array(dfdata['hessvec']).reshape((3*natoms, -1),
                                                             order='F')
            redmas = np.einsum('ij,ij,i->j', eigvec, eigvec, atmas)
            if mweigh:
                # Simply correct the normalization, already weighted by default
                eigvec[:, ...] = eigvec[:, ...] / np.sqrt(redmas)
            else:
                eigvec[:, ...] = eigvec[:, ...]*np.sqrt(atmas) / \
                    np.sqrt(redmas)
        else:
            raise NotImplementedError('Diagonalization NYI')
    if get_eval:
        if hessval is not None or 'hessval' in dfdata:
            if hessval is not None:
                eigval = np.array(hessval)
            else:
                eigval = np.array(dfdata['hessval'])
        else:
            raise NotImplementedError('Diagonalization NYI')

    # We transpose eigvec to have a C/Python compliant data order
    return eigvec.transpose(), eigval
