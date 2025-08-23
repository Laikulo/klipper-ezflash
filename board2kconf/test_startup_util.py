from unittest import TestCase

from .startup_util import DistVersion


class TestVersion(TestCase):
    def test_rel_compare_value(self):
        for test_case in (
                ((1,2,3,4), (4,4,4), (1,2,3)),
                ((1,2,3,4), (4,4,4,4,4,4), (1,2,3,4,0,0)),
                ((1,2,3,4), (4,4,4,4), (1,2,3,4)),
                ((1,2,3,4), (4,), (1,))
            ):
            with self.subTest(this_version=test_case[0], other_version=test_case[1]):
                self.assertEqual(
                    test_case[2],
                    DistVersion(release=test_case[0]).rel_compare_value(test_case[1])
                )
