"""Examples of module specific rules (similar in spirit to DaVinci's COMxxxxx / OSxxxxx checks)."""
from ecucstudio.rulekit import containers, param_obj, parse_float, pdef, value, values
from ecucstudio.validation import Result, Rule, Severity, SolvingAction

COM_TXMODE = ("/MICROSAR/Com/ComConfig/ComIPdu/ComTxIPdu/ComTxModeTrue/ComTxMode",
              "/MICROSAR/Com/ComConfig/ComIPdu/ComTxIPdu/ComTxModeFalse/ComTxMode")


class ComTxPeriodRule(Rule):
    """PERIODIC/MIXED transmission modes need ComTxModeTimePeriod > 0."""
    id = "EST10001"
    title = "Cyclic transmission mode without period"

    def check(self, ctx):
        for dp in COM_TXMODE:
            for c, path in containers(ctx, dp):
                mode = value(c, "ComTxModeMode")
                if mode not in ("PERIODIC", "MIXED"):
                    continue
                per = parse_float(value(c, "ComTxModeTimePeriod", "") or "")
                if per is None or per <= 0:
                    d = pdef(ctx, c, "ComTxModeTimePeriod")
                    acts = []
                    if d is not None:
                        acts.append(SolvingAction("Set ComTxModeTimePeriod to 0.1 s (100 ms)",
                                                  lambda c=c, d=d: ctx.model.set_value(c, d, "0.1"),
                                                  preferred=True))
                    yield Result(self.id, Severity.ERROR, self.title,
                                 f"{path} uses ComTxModeMode={mode} but ComTxModeTimePeriod is "
                                 f"{'missing' if per is None else per}. The I-PDU would never be sent cyclically.",
                                 obj=param_obj(path, "ComTxModeTimePeriod", 0), element=c,
                                 definition=dp + "/ComTxModeTimePeriod", actions=acts)


class OsTaskStackRule(Rule):
    """OsTaskStackSize should be a multiple of 8 on TriCore (stack alignment)."""
    id = "EST20001"
    title = "Task stack size not 8-byte aligned"

    def check(self, ctx):
        for c, path in containers(ctx, "/MICROSAR/Os/OsTask"):
            for i, v in enumerate(values(c, "OsTaskStackSize")):
                from ecucstudio.project import raw_value
                raw = raw_value(v) or ""
                try:
                    n = int(raw, 0)
                except ValueError:
                    continue
                if n % 8:
                    d = pdef(ctx, c, "OsTaskStackSize")
                    fixed = str((n + 7) // 8 * 8)
                    yield Result(self.id, Severity.WARNING, self.title,
                                 f"The parameter {param_obj(path, 'OsTaskStackSize', i, raw)} is not a multiple "
                                 f"of 8 bytes.", obj=param_obj(path, "OsTaskStackSize", i), element=v,
                                 definition="/MICROSAR/Os/OsTask/OsTaskStackSize",
                                 actions=[SolvingAction(f"Round up to {fixed}",
                                                        lambda c=c, d=d, f=fixed, i=i: ctx.model.set_value(c, d, f, i),
                                                        preferred=True)] if d else [])


RULES = [ComTxPeriodRule(), OsTaskStackRule()]
