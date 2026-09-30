"""Local immutable rule releases for audit. No production activation API."""
import json
from pathlib import Path
from sales_coach.domain.deposit_attribution import validate_rules
from sales_coach.domain.intelligence import fingerprint


class AttributionRuleRepository:
    def __init__(self, root):
        self.root = Path(root)

    def save_release(self, release):
        if release.get("mode") != "simulation_only" or release.get("production_active") is not False:
            raise ValueError("Only inactive simulation releases are supported")
        validate_rules(release["rules"])
        if not isinstance(release.get("version"), int) or release["version"] < 1:
            raise ValueError("Invalid release version")
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"v{release['version']}.json"
        earlier = sorted((p for p in self.root.glob("v*.json") if p.stem[1:].isdigit()
                          and int(p.stem[1:]) < release["version"]), key=lambda p: int(p.stem[1:]))
        if earlier:
            previous = {r["rule_id"]: r for r in json.loads(earlier[-1].read_text())["rules"]}
            for rule in release["rules"]:
                old = previous.get(rule["rule_id"])
                if old and not old["simulation_enabled"] and not old["valid_from"] and rule["simulation_enabled"] and not rule["valid_from"]:
                    raise ValueError("A pending rule needs an effective date before enabling simulation")
                if old and fingerprint(old) != fingerprint(rule) and rule["version"] <= old["version"]:
                    raise ValueError("Changing a rule requires a new rule version")
        content = json.dumps(release, ensure_ascii=False, indent=2) + "\n"
        if path.exists():
            if fingerprint(json.loads(path.read_text())) != fingerprint(release):
                raise ValueError("Immutable release already exists; create a new version")
        else:
            with path.open("x") as handle:
                handle.write(content)
        return path

    def load_release(self, version):
        version = int(version)
        release = json.loads((self.root / f"v{version}.json").read_text())
        if release.get("production_active") is not False or release.get("mode") != "simulation_only":
            raise ValueError("Production rules cannot be used in this simulator")
        validate_rules(release["rules"])
        return release
