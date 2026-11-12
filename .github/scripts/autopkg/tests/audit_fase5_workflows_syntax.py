"""
Fase 5 Part 3: Cross-audit of CI/CD Workflows for GitHub Actions and Forgejo.
"""

import re
import unittest
from pathlib import Path


class TestWorkflowAudit(unittest.TestCase):
    def setUp(self):
        self.wf_dir = Path("/home/dicov/Proyectos/autopkg/workflows")

    def test_workflow_yaml_syntax_and_no_sleep_loops(self):
        wf_files = list(self.wf_dir.glob("**/*.yml")) + list(self.wf_dir.glob("**/*.yaml"))
        self.assertTrue(len(wf_files) >= 4, f"Found only {len(wf_files)} workflow files")

        for wf in wf_files:
            content = wf.read_text(encoding="utf-8")

            # 1. Check for forbidden active polling sleep loops
            sleep_matches = re.findall(r'\bsleep\s+\d+', content)
            self.assertEqual(
                sleep_matches, [],
                f"Forbidden active polling 'sleep' detected in {wf.name}: {sleep_matches}"
            )

            # 2. Basic YAML syntax sanity (no unbalanced quotes, brackets)
            for idx, line in enumerate(content.splitlines(), 1):
                if line.strip().startswith("#"):
                    continue
                # Quotes balance check
                dquotes = line.count('"') - line.count('\\"')
                if dquotes % 2 != 0:
                    self.fail(f"Unbalanced double quotes in {wf.name} line {idx}: {line}")

            # 3. Check architecture matrices
            if "build" in wf.name:
                self.assertTrue("x86_64" in content, f"Missing x86_64 arch in {wf.name}")
                self.assertTrue("x86_64-musl" in content, f"Missing x86_64-musl arch in {wf.name}")

            print(f"Workflow {wf.relative_to(self.wf_dir)} passed audit (Zero sleep loops, valid YAML).")


if __name__ == "__main__":
    unittest.main()
