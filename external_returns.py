"""Known primitive-returning APIs are not constructor-result receivers.

These path functions return str/bytes (or raise); they cannot return an instance
of the function itself. Unknown return values remain unknown, without guessing
additional member targets. No dependency module is imported to obtain types.
"""
PRIMITIVE_RETURN_APIS = frozenset({
    'os.path.abspath', 'os.path.basename', 'os.path.dirname', 'os.path.join',
    'os.path.normpath', 'os.path.realpath', 'os.path.relpath',
    'os.path.expanduser', 'os.path.expandvars',
})

# These documented factories return an object of a different API type. Without
# indexing its defining class, retain an opaque result rather than inventing
# members on the factory callable. This is intentionally distinct from a
# primitive-return contract and does not claim the factory returns None.
# Source: https://py.readthedocs.io/en/stable/path.html#py.path.local.mkdtemp
OPAQUE_FACTORY_RETURN_APIS = frozenset({'py.path.local.mkdtemp'})
NON_CONSTRUCTOR_RETURN_APIS = PRIMITIVE_RETURN_APIS | OPAQUE_FACTORY_RETURN_APIS
