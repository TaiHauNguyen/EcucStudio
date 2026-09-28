"""Generic AUTOSAR rules that are not part of DaVinci's basic rule set."""
import re
from ecucstudio.project import definition_ref
from ecucstudio.validation import Result, Rule, Severity

SHORTNAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,127}$")


class ShortNameRule(Rule):
    """Container short names must be valid AUTOSAR identifiers (they become C symbols)."""
    id = "EST00002"
    title = "Invalid short name"

    def check(self, ctx):
        for path, el in ctx.model.path_index.items():
            name = path.rsplit("/", 1)[-1]
            if not SHORTNAME_RE.match(name):
                yield Result(self.id, Severity.ERROR, self.title,
                             f"The short name '{name}' of {path} is not a valid identifier "
                             f"([a-zA-Z][a-zA-Z0-9_]*, max. 128 characters).", obj=path, element=el,
                             definition=definition_ref(el))


# Note: a generic "handle id must be unique" rule was evaluated and dropped: MICROSAR uses
# separate id ranges (e.g. Rx/Tx I-PDUs) and recalculates ids during generation, so it
# produced ~2000 false positives on a real project.
RULES = [ShortNameRule()]
