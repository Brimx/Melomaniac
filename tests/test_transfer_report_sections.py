import unittest

from core.models import Track
from core.transfer import TransferIssue
from ui.dialogs.transfer_report import TransferReportDialog


class SectionsRegression(unittest.TestCase):
    def test_build_sections_no_nameerror(self):
        dlg = TransferReportDialog.__new__(TransferReportDialog)
        dlg._build_issue_row = lambda issue: None
        issue = TransferIssue(track=Track(id="1", name="t", artist="a", album="", duration="", img_url="", platform="ytmusic"), kind="not_found")
        dlg._build_sections([issue])


if __name__ == "__main__":
    unittest.main()
