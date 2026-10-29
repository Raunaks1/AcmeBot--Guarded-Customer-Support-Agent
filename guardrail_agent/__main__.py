"""Interactive demo:  python -m guardrail_agent [--llm mock|anthropic] [--no-guardrails]"""
import argparse

from .agent import GuardedAgent
from .llm import make_llm


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", default="mock", choices=["mock", "anthropic"])
    ap.add_argument("--no-guardrails", action="store_true")
    a = ap.parse_args()
    agent = GuardedAgent(make_llm(a.llm), guardrails=not a.no_guardrails)
    print(f"AcmeBot ({agent.llm.name}, guardrails={'off' if a.no_guardrails else 'on'}). Ctrl-C to quit.")
    while True:
        try:
            q = input("\nyou> ")
        except (EOFError, KeyboardInterrupt):
            break
        r = agent.run(q)
        print(f"[{r.status}] {r.answer}")
        for e in r.events:
            print(f"   guardrail:{e.layer}:{e.action} {e.detail}")
        if r.status == "pending_confirmation":
            ok = input("Approve this action? [y/N] ").strip().lower() == "y"
            r2 = agent.confirm(r.pending_id, ok)
            print(f"[{r2.status}] {r2.answer}")


if __name__ == "__main__":
    main()
