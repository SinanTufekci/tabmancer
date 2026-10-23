"""Every dialog has the one look: a frame of one of three widths, the same height limit, a title
line first and its keys last."""

from __future__ import annotations

import re

from tabmancer.app import LimitDialog, NamePrompt, PromptDialog, WorkspaceList
from tabmancer.background import BackgroundDialog
from tabmancer.cleanup import Confirm
from tabmancer.clisettings import CliSettings
from tabmancer.dialogs import WIDTHS, title
from tabmancer.handoverview import HandoverDialog
from tabmancer.keysview import KeysScreen
from tabmancer.newsessions import NewSessions
from tabmancer.statsview import StatsScreen
from tabmancer.teamview import LineDialog, NewTeamDialog, TeamsDialog

DIALOGS = [NamePrompt, PromptDialog, WorkspaceList, LimitDialog, BackgroundDialog, Confirm, CliSettings,
           HandoverDialog, KeysScreen, NewSessions, StatsScreen, LineDialog, TeamsDialog, NewTeamDialog]


def test_every_dialog_is_one_of_three_widths_and_as_tall_at_most():
    for dialog in DIALOGS:
        box = re.search(r"> Vertical \{ width: (\d+); max-width: 95%; height: [^;]+; max-height: 90%;",
                        dialog.DEFAULT_CSS)
        assert box is not None, dialog.__name__
        assert int(box[1]) in WIDTHS.values(), dialog.__name__
        # nothing of its own makes it wider or narrower than its size
        widths = re.findall(rf"^\s*{dialog.__name__} > Vertical \{{[^}}]*?(?<![-\w])width: (\d+);", dialog.DEFAULT_CSS,
                            re.MULTILINE)
        assert set(widths) == {box[1]}, dialog.__name__
    assert "round $warning" in Confirm.DEFAULT_CSS  # it deletes: amber


def test_a_title_is_bold_with_what_it_is_about_beside_it():
    line = title("Hand over", ("api-server", "bold cyan"), "“Rate limiting”")
    assert line.plain == "Hand over  api-server · “Rate limiting”"
    assert line.style == "bold" and any(str(span.style) == "bold cyan" for span in line.spans)
