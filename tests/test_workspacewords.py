"""What the dashboard says about workspaces, with no UI (workspacewords.py): w's name prompt, o's list and
its keys, and the notes as one is saved, deleted or opened."""

from tabmancer import workspacewords as WW
from tabmancer.workspaces import NEW, Entry, Workspace

PCB = Workspace("PCB work", [Entry("C:/work/pcb"), Entry("C:/work/web", NEW)])


def test_the_name_prompt_and_the_list():
    assert WW.name_heading(1) == "Name for a workspace with this session's folder"
    assert WW.name_heading(3) == "Name for a workspace with these 3 sessions' folders"
    assert "".join(words for words, _ in WW.option_parts(PCB)) == "PCB work   pcb, web"
    assert [key for key, _ in WW.LIST_KEYS] == ["Enter", "d", "Esc"]
    assert [key for key, _ in WW.NAME_KEYS] == ["Enter", "Esc"] and WW.PLACEHOLDER == "e.g. PCB work"


def test_the_notes():
    assert WW.saved_note(PCB).words == "Saved workspace 'PCB work': pcb, web"
    assert WW.deleted_note("PCB work").words == "Deleted workspace 'PCB work'."
    refused = WW.not_kept_note("PCB work", deleting=True)
    assert refused.severity == "error"
    assert refused.words.startswith("Workspace 'PCB work' not deleted: workspaces.json could not be read")
    assert WW.not_kept_note("x").words.startswith("Workspace 'x' not saved: ")
    assert WW.nothing_note([]).words == "Nothing to open."
    assert WW.nothing_note(["web: already open", "gone: folder not found"]).words == (
        "web: already open\ngone: folder not found")
    assert WW.tick_first_note().severity == WW.none_yet_note().severity == "warning"
