import sys

def test_sys_path():
    print("\nPATH:")
    for p in sys.path:
        print(" ", p)

def test_import_domain():
    from finanalytics_ai.domain.value_objects.money import Money
    assert Money is not None
