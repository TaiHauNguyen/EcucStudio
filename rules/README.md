# EcucStudio rule plugins

Every `*.py` file in this folder is loaded at start-up (files starting with `_` are
ignored). A plugin exposes `RULES = [MyRule(), ...]` where each rule derives from
`ecucstudio.validation.Rule` and implements `check(ctx)` returning `Result` objects.

```python
from ecucstudio.validation import Rule, Result, Severity, SolvingAction
from ecucstudio.rulekit import containers, value, param_obj

class TxPeriodRule(Rule):
    id = "EST10001"
    title = "Periodic transmission needs a period"

    def check(self, ctx):
        for c, path in containers(ctx, "/MICROSAR/Com/ComConfig/ComIPdu/ComTxIPdu/ComTxModeTrue/ComTxMode"):
            ...
            yield Result(self.id, Severity.ERROR, self.title, "message", obj=path, element=c)

RULES = [TxPeriodRule()]
```

Conventions
- IDs of this tool start with `EST` (DaVinci ids keep their origin: `AR-ECUC…`, `COM…`).
- Use DaVinci object notation for `obj`: `<container path>[<index>:<param>]`.
- Solving actions receive no arguments and must use the model API
  (`ctx.model.set_value`, `add_container`, `delete_element`) so that undo works.
- Rules run in a worker thread: never touch the GUI from a rule.
