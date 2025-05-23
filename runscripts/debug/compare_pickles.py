"""
Compare two .cPickle files or all the .cPickle files in a pair of directories.
Show the differences or optionally just a count of difference lines.

Usage (PATH is a path like 'out/manual/intermediates'):
    runscripts/debug/comparePickles.py PATH1 PATH2
"""

import argparse
from collections.abc import Mapping, Sequence
import functools
import numbers
import os
import pickle
from pprint import pformat
import re
import sys
import types
from typing import Dict

import Bio.Seq
import numpy as np
import scipy.interpolate
import sympy
from sympy.matrices import dense
import unum

from wholecell.utils import constants
import wholecell.utils.unit_struct_array


NULP = 0  # float comparison tolerance, in Number of Units in the Last Place

# Objects with a list of attributes to compare
SPECIAL_OBJECTS = {
    scipy.interpolate.CubicSpline: ["x", "c", "axis"],
    wholecell.utils.unit_struct_array.UnitStructArray: ["struct_array", "units"],
}

LEAF_TYPES = (
    unum.Unum,
    Bio.Seq.Seq,
    sympy.Basic,
    numbers.Number,
    functools.partial,
    types.FunctionType,
    dense.MutableDenseMatrix,
    wholecell.utils.unit_struct_array.UnitStructArray,
)

WHITESPACE = re.compile(r"\s+")


class Repr(object):
    """A Repr has the given repr() string without quotes and != any other value."""

    def __init__(self, repr_):
        self.repr_ = repr_

    def __repr__(self):
        return self.repr_


def has_python_vars(obj):
    """
    Returns true if the given object has any Python instance variables, that is
    ordinary fields or compact slots. If not, it's presumably a built-in type
    or extension type implemented entirely in C and Cython.
    """
    return hasattr(obj, "__dict__") or hasattr(obj, "__slots__")


def all_vars(obj):
    """
    Returns a dict of all the object's instance variables stored in ordinary
    fields and in compact slots. This expands on the built-in function `vars()`.
    If the object implements the pickling method `__getstate__`, call that
    instead to get its defining state.
    """
    if hasattr(obj, "__getstate__"):
        # noinspection PyCallingNonCallable
        return obj.__getstate__()

    attrs = getattr(obj, "__dict__", {})
    attrs.update({key: getattr(obj, key) for key in getattr(obj, "__slots__", ())})
    return attrs


def is_leaf(value, leaves=LEAF_TYPES):
    """
    Predicate to determine if we have reached the end of how deep we want to traverse
    through the object tree.
    """
    if isinstance(value, (Mapping, Sequence)):
        return isinstance(value, (bytes, str))
    return (
        callable(value)  # it's callable
        or isinstance(value, leaves)  # it's an instance of a declared leaf type
        or not has_python_vars(value)
    )  # an object without Python instance variables


def object_tree(obj, path="", debug=None):
    """
    Diagnostic tool to inspect a complex data structure.

    Given an object, exhaustively traverse down all attributes it contains until leaves
    are	reached, and convert everything found into a dictionary or a list. The resulting
    dictionary will mirror the structure of the original object, but instead of
    attributes with values it will be a dictionary where the keys are the attribute
    names. The type of the dictionarified object will be encoded under the key `!type`
    which is assumed to not be in conflict with any other attributes. The result should
    aid in serialization and deserialization of the object and is intended to be a
    translation of a pickled object.

    Args:
        obj (object): The object to inspect.
        path (optional str): The root path of this object tree. This will be built upon
            for each child of the current object found and reported in a value is
            provided for `debug`.
        debug (optional str): If provided, prints paths of the attributes encountered.
            If the value is 'ALL', it will print every path. If the value is 'CALLABLE',
            it will only print methods and functions it finds.
    """

    if debug == "ALL":
        print(path)

    if is_leaf(obj):
        if callable(obj) and (debug == "CALLABLE"):
            print("{}: {}".format(path, obj))
        return obj
    elif isinstance(obj, Mapping):
        return {
            key: object_tree(value, "{}['{}']".format(path, key), debug)
            for (key, value) in obj.items()
        }
    elif isinstance(obj, Sequence):
        return [
            object_tree(subobj, "{}[{}]".format(path, index), debug)
            for index, subobj in enumerate(obj)
        ]
    else:
        attrs = all_vars(obj)
        tree = {
            key: object_tree(value, "{}.{}".format(path, key), debug)
            for (key, value) in attrs.items()
        }
        tree["!type"] = type(obj)

        return tree


def size_tree(o, cutoff=0.1):
    """
    Find the size of attributes in an object tree. Sizes greater than the cutoff
    (in MB) will be returned for displaying. Sizes include all values contained
    within an attribute (eg. a Dict will be represented by the size of all keys
    and values in addition to the Dict size itself).

    TODO: double check total size vs disk size - might be missing some types
    """

    def return_val(total, value):
        if total > cutoff and value:
            return total, value
        else:
            return (total,)

    def get_size(o):
        return sys.getsizeof(o) / 2**20  # convert to MB

    size = get_size(o)

    # special handling of leaf to get size of defining attributes
    if isinstance(o, unum.Unum):
        size += size_tree(o._unit)[0]
        size += get_size(o._value)
        return (size,)

    # special handling of leaf to get size of str sequence
    elif isinstance(o, Bio.Seq.Seq):
        size += get_size(o._data)
        return (size,)

    # special handling of leaf, each entry is allocated the same amount of space
    elif isinstance(o, wholecell.utils.unit_struct_array.UnitStructArray):
        size += size_tree(o.units)[0]
        n_entries = len(o.struct_array)
        if n_entries:
            size += get_size(o.struct_array[0]) * n_entries
        return (size,)

    # if a special object, check predefined attributes for equality
    elif type(o) in SPECIAL_OBJECTS:
        sizes = {}
        attrs = SPECIAL_OBJECTS[type(o)]
        for attr in attrs:
            subsizes = size_tree(getattr(o, attr), cutoff)
            size += subsizes[0]
            if subsizes[0] > cutoff:
                formatted = float("{:.2f}".format(subsizes[0]))
                if len(subsizes) == 1:
                    val = formatted
                else:
                    val = (formatted, subsizes[1])
                sizes[attr] = val
        return return_val(size, sizes)

    # if it is a leaf, just return the size
    # TODO: any special handling for types that are not already accounted for above
    elif is_leaf(o):
        return (size,)

    # if it is a dictionary, then get the size of keys and values
    elif isinstance(o, Mapping):
        sizes = {}
        total_size = size
        for key, value in o.items():
            subsizes = size_tree(value, cutoff)
            entry_size = subsizes[0] + get_size(key)
            total_size += entry_size
            if entry_size > cutoff:
                formatted = float("{:.2f}".format(entry_size))
                if len(subsizes) == 1:
                    val = formatted
                else:
                    val = (formatted, subsizes[1])
                sizes[key] = val
        return return_val(total_size, sizes)

    # if it is a sequence, then get the size of each element
    elif isinstance(o, Sequence):
        sizes = []
        total_size = size
        for value in o:
            subsizes = size_tree(value, cutoff)
            total_size += subsizes[0]
            if subsizes[0] > cutoff:
                formatted = float("{:.2f}".format(subsizes[0]))
                if len(subsizes) == 1:
                    val = formatted
                else:
                    val = (formatted, subsizes[1])
                sizes.append(val)
        return return_val(total_size, sizes)

    else:
        return (size,)


def _are_instances_of(a, b, a_type):
    """
    Return True if `a` and `b` are both instances of the given type (or tuple
    of types).
    """
    return isinstance(a, a_type) and isinstance(b, a_type)


def diff_trees(a, b):
    """
    Find the differences between two trees or leaf nodes a and b. Return a
    falsey value if the inputs match OR a truthy value that explains or
    summarizes their differences, where each point in the tree where the inputs
    differ will be a tuple (a's value, b's value, optional description).

    Floating point numbers are compared with the tolerance set by the constant
    NULP (Number of Units in the Last Place), allowing for NaN and infinite
    values. (Adjust the tolerance level NULP if needed.)

    This operation is symmetrical.
    """

    # treat str and Python 2 unicode as the same leaf type
    # ditto for int and Python 2 long
    if _are_instances_of(a, b, str) or _are_instances_of(a, b, int):
        if a != b:
            return elide(a), elide(b)

    # if they aren't they same type, they are clearly different. Also this lets us
    # safely assume throughout the rest of the function that a and b are the same type
    elif type(a) is not type(b):
        return elide(a, max_len=400), elide(b, max_len=400)

    # if they are floats, handle various kinds of values
    elif isinstance(a, float):
        return compare_floats(a, b)

    # if they are numpy arrays, compare them using a numpy testing function
    elif isinstance(a, np.ndarray):
        return compare_ndarrays(a, b)

    # if they are Unums compare their contents with matching units
    elif isinstance(a, unum.Unum):
        a0, b0 = a.matchUnits(b)
        return diff_trees(a0.asNumber(), b0.asNumber())

    # if a special object, check predefined attributes for equality
    elif type(a) in SPECIAL_OBJECTS:
        diff = {}
        attrs = SPECIAL_OBJECTS[type(a)]
        for attr in attrs:
            subdiff = diff_trees(getattr(a, attr), getattr(b, attr))
            if subdiff:
                diff[attr] = subdiff
        return diff

    # if they are leafs (including strings) use python equality comparison
    elif is_leaf(a):
        if a != b:
            return elide(a), elide(b)

    # if they are dictionaries then diff the value under each key
    elif isinstance(a, Mapping):
        diff = {}
        na = Repr("--")
        nb = Repr("--")
        for key in set(a.keys()) | set(b.keys()):
            subdiff = diff_trees(a.get(key, na), b.get(key, nb))
            if subdiff:
                diff[key] = subdiff
        return diff

    # if they are sequences then compare each element at each index
    elif isinstance(a, Sequence):
        if len(a) > len(b):
            b = list(b) + (len(a) - len(b)) * [Repr("--")]
        elif len(b) > len(a):
            a = list(a) + (len(b) - len(a)) * [Repr("--")]

        diff = []
        for index in range(len(a)):
            subdiff = diff_trees(a[index], b[index])
            if subdiff:
                diff.append(subdiff)
        return diff

    # this should never happen
    else:
        print("value not considered by `diff_trees`: {} {}".format(a, b))


def elide(value, max_len=200):
    """Return a value with the same repr but elided if it'd be longer than max."""
    repr_ = repr(value)
    if len(repr_) > max_len:
        return Repr(repr_[:max_len] + "...")
    return value


def simplify_error_message(message):
    return elide(Repr(WHITESPACE.sub(" ", message).strip()))


def compare_floats(f1, f2):
    """Compare two floats, allowing some tolerance, NaN, and Inf values. This
    considers all types of NaN to match.
    Return 0.0 (which is falsey) if they match, else (f1, f2).
    """
    if f1 == f2 or np.isnan(f1) and np.isnan(f2):
        return 0.0
    try:
        np.testing.assert_array_almost_equal_nulp(f1, f2, nulp=NULP)
        return 0.0
    except AssertionError:
        # FWIW, the string error.args[0] tells the NULP difference.
        return f1, f2


def compare_ndarrays(array1, array2):
    """Compare two ndarrays, checking the shape and all elements, allowing for
    NaN values and non-numeric values. Return () if they match, else a tuple of
    diff info or just a diff description.

    TODO(jerry): Allow tolerance for float elements of structured arrays and
      handle NaN and Inf values.
    """

    def summarize_array(ndarray):
        return Repr(f"array({ndarray.shape} {ndarray.dtype})")

    if array1.shape != array2.shape:
        return summarize_array(array1), summarize_array(array2)

    object_dtype = np.dtype(object)
    if issubclass(array1.dtype.type, np.floating):
        try:
            # This handles float tolerance but not NaN and Inf.
            with np.errstate(invalid="ignore"):
                np.testing.assert_array_almost_equal_nulp(array1, array2, nulp=NULP)
            return ()
        except AssertionError as _:
            # return elide(array1), elide(array2), simplify_error_message(e.args[0])
            pass  # try again, below
    # Handle ragged arrays created with an object dtype
    elif array1.dtype == object_dtype and array2.dtype == object_dtype:
        try:
            assert array1.shape == array2.shape
            for sub1, sub2 in zip(array1, array2):
                np.testing.assert_equal(sub1, sub2)
            return ()
        except AssertionError as e:
            return simplify_error_message(e.args[0])

    try:
        # This handles non-float dtypes, also NaN and Inf, but no tolerance.
        np.testing.assert_array_equal(array1, array2)
        return ()
    except AssertionError as e:
        return simplify_error_message(e.args[0])


def load_tree(path):
    """Load a .cPickle file as an object_tree."""
    with open(path, "rb") as f:
        data = pickle.load(f, fix_imports=True, encoding="latin1")
    return object_tree(data)


def load_fit_tree(out_subdir):
    """Load the parameter calculator's (Parca's) output as an object_tree."""
    # For convenience, optionally add the prefix 'out/'.
    if not os.path.isabs(out_subdir) and not os.path.isdir(out_subdir):
        out_subdir = os.path.join("out", out_subdir)

    path = os.path.join(
        out_subdir, constants.KB_DIR, constants.SERIALIZED_SIM_DATA_FILENAME
    )

    return load_tree(path)


def pprint_diffs(diffs, *, width=160, print_diff_lines=True, print_count=True):
    """Pretty-print the diff info: optionally print the detailed diff lines,
    optionally print the diff line count as a single figure of merit; then
    return the line count.
    """
    if diffs:
        diff_lines = pformat(diffs, width=width)
        if print_diff_lines:
            print(diff_lines)
        line_count = len(diff_lines.strip().splitlines())
    else:
        line_count = 0

    if print_count:
        print("==> lines of differences: {}".format(line_count))
    return line_count


def diff_files(path1: str, path2: str, print_diff_lines: bool = True) -> int:
    """Diff the pair of named pickle files. Return the diff line count."""
    tree1 = load_tree(path1)
    tree2 = load_tree(path2)
    diffs = diff_trees(tree1, tree2)
    return pprint_diffs(diffs, print_diff_lines=print_diff_lines)


def list_pickles(directory: str) -> Dict[str, str]:
    """Return a map of .cPickle file names to paths in the given directory
    sorted by file modification time then by filename."""
    entries = [
        (entry.stat().st_mtime, entry.name, entry.path)
        for entry in os.scandir(directory)
        if entry.is_file() and entry.name.endswith(".cPickle")
    ]
    files = {e[1]: e[2] for e in sorted(entries)}
    return files


def diff_dirs(dir1: str, dir2: str, print_diff_lines: bool = True) -> int:
    """Diff the pickle files in the pair of named directories. Return the total
    diff line count."""
    print(f'Comparing pickle files in "{dir1}" vs. "{dir2}".')
    pickles1 = list_pickles(dir1)
    pickles2 = list_pickles(dir2)
    count = 0

    for name, path1 in pickles1.items():
        print(f"\n*** {name} {'*' * (75 - len(name))}")
        path2 = pickles2.get(name)
        if path2:
            count += diff_files(path1, path2, print_diff_lines)
        else:
            print(f"{name} is in {dir1} but not {dir2}")
            count += 1

    only_in_dir2 = pickles2.keys() - pickles1.keys()
    if only_in_dir2:
        print(f"\n*** Pickle files in {dir2} but not {dir1}:\n{sorted(only_in_dir2)}")
        count += len(only_in_dir2)

    print(
        f"\n====> Total differences: {count} lines for {len(pickles1)} pickle"
        f" files in {dir1} against {len(pickles2)} pickle files in {dir2}."
    )
    return count


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Compare two .cPickle files"
        " or all the .cPickle files in two directories (in"
        " modification-time order)."
        " Print a count and optionally a summary of the differences."
    )
    parser.add_argument(
        "-c",
        "--count",
        action="store_true",
        help="Print just the diff line count for each file, skipping the"
        " detailed diff lines.",
    )
    parser.add_argument(
        "-f",
        "--final-sim-data",
        action="store_true",
        help="Append /kb/simData.cPickle to the two PATH args to make it a"
        " little easier compare the final Parca output sim_data.",
    )
    parser.add_argument(
        "path",
        metavar="PATH",
        nargs=2,
        help="The two pickle files or directories to compare.",
    )

    args = parser.parse_args()
    path1, path2 = args.path

    if args.final_sim_data:
        path1 = os.path.join(
            path1, constants.KB_DIR, constants.SERIALIZED_SIM_DATA_FILENAME
        )
        path2 = os.path.join(
            path2, constants.KB_DIR, constants.SERIALIZED_SIM_DATA_FILENAME
        )

    if os.path.isfile(path1):
        diff_count = diff_files(path1, path2, print_diff_lines=not args.count)
    else:
        diff_count = diff_dirs(path1, path2, print_diff_lines=not args.count)

    sys.exit(3 if diff_count else 0)
