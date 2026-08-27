import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class GrafanaAssetsTest(unittest.TestCase):
    def test_dashboard_is_valid_and_contains_correlation_views(self):
        dashboard_path = ROOT / "grafana" / "dashboards" / "conversation-correlations.json"
        dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))
        panels = dashboard["panels"]

        self.assertEqual(dashboard["uid"], "conversation-correlation")
        self.assertEqual(len({panel["id"] for panel in panels}), len(panels))
        self.assertGreaterEqual(sum(panel["type"] == "xychart" for panel in panels), 2)
        self.assertTrue(any("CORR(" in target.get("rawSql", "") for panel in panels for target in panel.get("targets", [])))

        variables = {item["name"] for item in dashboard["templating"]["list"]}
        self.assertEqual(variables, {"status", "model"})
        self.assertFalse(
            any(
                "${user" in target.get("rawSql", "")
                for panel in panels
                for target in panel.get("targets", [])
            )
        )

        datasource_uids = {
            panel["datasource"]["uid"]
            for panel in panels
            if isinstance(panel.get("datasource"), dict)
        }
        self.assertEqual(datasource_uids, {"conversation-postgres"})


if __name__ == "__main__":
    unittest.main()
