# LCK restoration manifest

This is the auditable old-path to new-path inventory for Task #333.

- Authoritative source commit: `0d9d762238a3e837a864b5350bf16d1b885a73c3`
- Source tree: `2e8b5d641bd2b9f230cfe4e114932d9bb9b6fc96`
- Removal commit: `8fd67a7d7ad0f012e52680d3572c9614169bd8d6`
- Equivalent archive tag: `tracequant-v1-archive-2026-09-12`
- Recovery method: Git object/path reads only; no revert, cherry-pick, or v1 tree overlay.

The blob column records the historical object when the old path is a complete file.
Rows marked omitted or responsibility replaced document why no old file is silently lost.

| Old path | Historical blob | New path | Status | Responsibility/rationale |
| --- | --- | --- | --- | --- |
| `tools/agent_workflow/bug_policy.py` | `59778f868989abd7441e20f79d01d240b6f319f0` | `tools/lck/bug_policy.py` | restored/adapted | Moved into the isolated LCK tooling root; imports and identity paths adapted. |
| `tools/agent_workflow/critical_outcome.py` | `d1de9c72d010bea17e9ab94fe918cee503e1e603` | `tools/lck/critical_outcome.py` | restored/adapted | Moved into the isolated LCK tooling root; imports and identity paths adapted. |
| `tools/agent_workflow/documentation_policy.py` | `22187c105166e1cf2d787e283bc3ba42a9c7208f` | `tools/lck/documentation_policy.py` | restored/adapted | Moved into the isolated LCK tooling root; imports and identity paths adapted. |
| `tools/agent_workflow/issue_form_contract.py` | `b019a121390126b5be681fad92f82c837c423125` | `tools/lck/issue_forms.py` | restored/adapted | Moved into the isolated LCK tooling root; imports and identity paths adapted. |
| `tools/agent_workflow/lck.py` | `6487b03b3817bd0c1414a9f5fddeed497313990a` | `tools/lck/__main__.py` | restored/adapted | Moved into the isolated LCK tooling root; imports and identity paths adapted. |
| `tools/agent_workflow/lck_core/README.md` | `70fd67f473beea7e61746efc42cfa329d3142355` | `docs/workflows/lck/implementation-map.md` | restored/adapted | Moved into the isolated LCK tooling root; imports and identity paths adapted. |
| `tools/agent_workflow/lck_core/__init__.py` | `451928f507d2ac41cfaa7d5e1b2df3058c6aeb34` | `tools/lck/__init__.py` | restored/adapted | Moved into the isolated LCK tooling root; imports and identity paths adapted. |
| `tools/agent_workflow/lck_core/cli.py` | `46485552dc9e04b4f0393f4fe0b81594a89520e2` | `tools/lck/cli.py` | restored/adapted | Moved into the isolated LCK tooling root; imports and identity paths adapted. |
| `tools/agent_workflow/lck_core/closeout.py` | `d4d67fed8f3e101051d0c77bcbbda3e61f615025` | `tools/lck/closeout.py` | restored/adapted | Moved into the isolated LCK tooling root; imports and identity paths adapted. |
| `tools/agent_workflow/lck_core/delivery.py` | `82ef5979f7f804b255293412f8a6f533fec9697d` | `tools/lck/delivery.py` | restored/adapted | Moved into the isolated LCK tooling root; imports and identity paths adapted. |
| `tools/agent_workflow/lck_core/effective_diff.py` | `dacffd950ff8e30bebc8bb06fe464d70f91a95cd` | `tools/lck/effective_diff.py` | restored/adapted | Moved into the isolated LCK tooling root; imports and identity paths adapted. |
| `tools/agent_workflow/lck_core/effects.py` | `744f4bc729f18f071410779a3df18ef7891008e9` | `tools/lck/effects.py` | restored/adapted | Moved into the isolated LCK tooling root; imports and identity paths adapted. |
| `tools/agent_workflow/lck_core/eligibility.py` | `0eefd4b2e9af18b7dfde9e35af59ee347cebe018` | `tools/lck/eligibility.py` | restored/adapted | Moved into the isolated LCK tooling root; imports and identity paths adapted. |
| `tools/agent_workflow/lck_core/issue_profiles.py` | `b46b752ede9acccb20900ec34f5b956865fbed61` | `tools/lck/issue_profiles.py` | restored/adapted | Moved into the isolated LCK tooling root; imports and identity paths adapted. |
| `tools/agent_workflow/lck_core/models.py` | `a75373d814946e8b039d884c5aca5f955a54eae9` | `tools/lck/models.py` | restored/adapted | Moved into the isolated LCK tooling root; imports and identity paths adapted. |
| `tools/agent_workflow/lck_core/profile_policies.py` | `ee94ac9560748bfc68eb686d4417bcacc4c233a1` | `tools/lck/profile_policies.py` | restored/adapted | Moved into the isolated LCK tooling root; imports and identity paths adapted. |
| `tools/agent_workflow/lck_core/receipts.py` | `52b7f720770e90e92dea951fb3aba97ed93a51e2` | `tools/lck/receipts.py` | restored/adapted | Moved into the isolated LCK tooling root; imports and identity paths adapted. |
| `tools/agent_workflow/lck_core/remediation.py` | `0f71b50afa4c2f1315bc16741bb3926d33830b21` | `tools/lck/remediation.py` | restored/adapted | Moved into the isolated LCK tooling root; imports and identity paths adapted. |
| `tools/agent_workflow/lck_core/review.py` | `8e08c371b18d22a5201b74871bdf13660c5620cf` | `tools/lck/review.py` | restored/adapted | Moved into the isolated LCK tooling root; imports and identity paths adapted. |
| `tools/agent_workflow/lck_core/review_authority.py` | `19678c2e9b747e47611e3073ccae8c350dad3910` | `tools/lck/review_authority.py` | restored/adapted | Moved into the isolated LCK tooling root; imports and identity paths adapted. |
| `tools/agent_workflow/lck_core/review_workspace.py` | `7e1380ba40941eb4ec6881e0e3ec3d9c7a42c1df` | `tools/lck/review_workspace.py` | restored/adapted | Moved into the isolated LCK tooling root; imports and identity paths adapted. |
| `tools/agent_workflow/lck_core/shared_facts.py` | `441e35889227b92082c60e054b94a4621a30029e` | `tools/lck/shared_facts.py` | restored/adapted | Moved into the isolated LCK tooling root; imports and identity paths adapted. |
| `tools/agent_workflow/lck_core/state.py` | `d3487855807a332a3d91828de8c81566f099b94e` | `tools/lck/state.py` | restored/adapted | Moved into the isolated LCK tooling root; imports and identity paths adapted. |
| `tools/agent_workflow/lck_core/structured_review_instructions.py` | `286ee82612623e89a9c67ebec8427196a36298b5` | `tools/lck/structured_review_instructions.py` | restored/adapted | Moved into the isolated LCK tooling root; imports and identity paths adapted. |
| `tools/agent_workflow/lck_core/validation.py` | `d95bceb4a6c82df208f3e1ba673dc224625ea2d4` | `tools/lck/validation_gates.py` | restored/adapted | Moved into the isolated LCK tooling root; imports and identity paths adapted. |
| `tools/agent_workflow/markdown_sections.py` | `565f224b77e5b43d20c4d126e7ed0bf158bffa07` | `tools/lck/markdown_sections.py` | restored/adapted | Moved into the isolated LCK tooling root; imports and identity paths adapted. |
| `tools/agent_workflow/pr_resolve.py` | `49e18571f92403bdad5476d203171ae79cc857cb` | `tools/lck/github_prs.py` | restored/adapted | Moved into the isolated LCK tooling root; imports and identity paths adapted. |
| `tools/agent_workflow/project_status.py` | `d74274dbfb05629c3987c7c9cea7040b034b47dc` | `tools/lck/github_projects.py` | restored/adapted | Moved into the isolated LCK tooling root; imports and identity paths adapted. |
| `tools/agent_workflow/research_policy.py` | `ed7ff0666612d604b581686e30c71cb7f5832982` | `tools/lck/research_policy.py` | restored/adapted | Moved into the isolated LCK tooling root; imports and identity paths adapted. |
| `tools/agent_workflow/skill_path_audit.py` | `c9465df1fd2550ddb4a6c6e427daa6683a30dd31` | `tools/lck/skill_audit.py` | restored/adapted | Reworked to audit one canonical `.agents` Skill source plus thin provider adapters instead of duplicated Skill bodies. |
| `tools/agent_workflow/workflow_common.py` | `840a78713ed02561e4be4558fb9ac4f9bfc38fb5` | `tools/lck/common.py` | restored/adapted | Moved into the isolated LCK tooling root; imports and identity paths adapted. |
| `tools/agent_workflow/workflow_evidence.py` | `4ba719efc2cb58bce59a2a3cccbf303fbed54ff0` | `tools/lck/feature_audit.py` | restored/adapted | Moved into the isolated LCK tooling root; imports and identity paths adapted. |
| `tools/agent_workflow/workflow_validation.py` | `3282d8c21e6a09e199443cbd9ddc8f52104c72bd` | `tools/lck/validation_runner.py` | restored/adapted | Moved into the isolated LCK tooling root; imports and identity paths adapted. |
| `tools/agent_workflow/wsl2_validation_profiles.json` | `3fea9933126f11fc009cc57187049442fc2e1e22` | `tools/lck/config/validation_profiles.json` | restored/adapted | Moved into the isolated LCK tooling root; imports and identity paths adapted. |
| `tools/agent_workflow/wsl2_validation_runner.py` | `65934b3270518da123eba0f702b68253b4844644` | `tools/lck/wsl2_validation_runner.py` | restored/adapted | Moved into the isolated LCK tooling root; imports and identity paths adapted. |
| `src/tracequant/contracts/review.py` | `72c2ba279e05a1b2e75b1c1071b81a927f273e76` | `tools/lck/review_models.py` | restored/adapted | Workflow Review contracts moved out of the product namespace. |
| `src/tracequant/contracts/__init__.py` | `2570dbda2b3b4db25c56ed7ebea246db774f662b` | — | omitted | Product-package re-export is no longer applicable; callers import tools.lck.review_models. |
| `src/tracequant/domain/models.py (DomainValidationError only)` | `see named source file` | `tools/lck/review_models.py (ValueError base)` | responsibility replaced | Avoids restoring the archived v1 domain model tree. |
| `src/tracequant/domain/__init__.py` | `9211195ddfd0d14449e946d7323fcdea8a5e5283` | — | omitted | Archived business-domain export is outside LCK scope. |
| `tests/tools/lck_test_support.py` | `668bbc6a895016512fe1f4db22514e4569fa8488` | `tests/tools/lck/support.py` | restored/adapted | Moved under the isolated LCK test root and updated for package imports/current v2 contracts. |
| `tests/tools/test_agent_neutral_workflow.py` | `2aba6101ebac23a9ef674edc6216dfd18ebf0ab2` | `tests/tools/lck/test_agent_neutral_workflow.py` | restored/adapted | Replaced duplicate-provider equality checks with canonical-source, thin-adapter, routing, and LCK-only documentation checks. |
| `tests/tools/test_critical_outcome.py` | `e799fc2c3da7aa463f19ea8aee11b9aa87bb23b5` | `tests/tools/lck/test_critical_outcome.py` | restored/adapted | Moved under the isolated LCK test root and updated for package imports/current v2 contracts. |
| `tests/tools/test_issue_form_contract.py` | `de9f5d1fd7700f27d5bf1ea1c898a18f47fd5001` | `tests/tools/lck/test_issue_form_contract.py` | restored/adapted | Moved under the isolated LCK test root and updated for package imports/current v2 contracts. |
| `tests/tools/test_issue_templates.py` | `ecb4f99b07577de734843520cfc4f79a6a7643ec` | `tests/tools/lck/test_issue_templates.py` | restored/adapted | Moved under the isolated LCK test root and updated for package imports/current v2 contracts. |
| `tests/tools/test_lck.py` | `b1442e793a1fd9dc91792e4dd7dee661ffb654be` | `tests/tools/lck/test_branch_naming.py` | restored/adapted | Moved under the isolated LCK test root and updated for package imports/current v2 contracts. |
| `tests/tools/test_lck_acceptance.py` | `aafd4f2ac637ddc42215d5474e0796a682b03023` | `tests/tools/lck/test_acceptance.py` | restored/adapted | Rewritten around the stable module CLI, required Task #333 Critical Outcome, new import boundary, typed profiles, adapters, and manifest. Detailed lifecycle behavior remains covered by the migrated phase tests. |
| `tests/tools/test_lck_bug.py` | `d5bc9c2df7ed8d404c2aaee849aebf085d19615d` | `tests/tools/lck/test_bug.py` | restored/adapted | Moved under the isolated LCK test root and updated for package imports/current v2 contracts. |
| `tests/tools/test_lck_closeout.py` | `0faa0c5ca3e4bbd4ba81e2463f2bf51e16826ebe` | `tests/tools/lck/test_closeout.py` | restored/adapted | Moved under the isolated LCK test root and updated for package imports/current v2 contracts. |
| `tests/tools/test_lck_closeout_additional.py` | `87cb0f25f40f339bff0edcca3b8b4b628a7fa2f3` | `tests/tools/lck/test_closeout_additional.py` | restored/adapted | Moved under the isolated LCK test root and updated for package imports/current v2 contracts. |
| `tests/tools/test_lck_delivery.py` | `2c721084ecef508fff6414ea8473b3855b59bb6a` | `tests/tools/lck/test_delivery.py` | restored/adapted | Moved under the isolated LCK test root and updated for package imports/current v2 contracts. |
| `tests/tools/test_lck_documentation.py` | `563c3a8664e3963a47dab13e36788b5fa35291b6` | `tests/tools/lck/test_documentation.py` | restored/adapted | Moved under the isolated LCK test root and updated for package imports/current v2 contracts. |
| `tests/tools/test_lck_issue_profiles.py` | `33ebda42cc6428d001c4209f77293ae5ec4220a2` | `tests/tools/lck/test_issue_profiles.py` | restored/adapted | Moved under the isolated LCK test root and updated for package imports/current v2 contracts. |
| `tests/tools/test_lck_prepare.py` | `8e4f9c1a47cdb778cde161603f2d12d078c727a9` | `tests/tools/lck/test_prepare.py` | restored/adapted | Moved under the isolated LCK test root and updated for package imports/current v2 contracts. |
| `tests/tools/test_lck_profile_architecture.py` | `bb112893d726e7196216404b4ba9506ddbbdcfdb` | `tests/tools/lck/test_profile_architecture.py` | restored/adapted | Moved under the isolated LCK test root and updated for package imports/current v2 contracts. |
| `tests/tools/test_lck_receipts.py` | `1b0396ff440cf40fe2eb6e101cde910afde9e7ed` | `tests/tools/lck/test_receipts.py` | restored/adapted | Moved under the isolated LCK test root and updated for package imports/current v2 contracts. |
| `tests/tools/test_lck_remediation.py` | `1693da725c4aeefa827c3b9bdc8ebb973bacc3a9` | `tests/tools/lck/test_remediation.py` | restored/adapted | Moved under the isolated LCK test root and updated for package imports/current v2 contracts. |
| `tests/tools/test_lck_research.py` | `65a1e8ea16251e6ffd99dfea93e1ea540074cc56` | `tests/tools/lck/test_research.py` | restored/adapted | Moved under the isolated LCK test root and updated for package imports/current v2 contracts. |
| `tests/tools/test_lck_review.py` | `039759394f7194dcb0f747580fe9e5e135eb4a89` | `tests/tools/lck/test_review.py` | restored/adapted | Moved under the isolated LCK test root and updated for package imports/current v2 contracts. |
| `tests/tools/test_lck_review_models.py` | `9c3bf632faec9b001e6d527968bd59d8ca6e11f3` | `tests/tools/lck/test_review_models.py` | restored/adapted | Moved under the isolated LCK test root and updated for package imports/current v2 contracts. |
| `tests/tools/test_lck_state.py` | `7e8c083f83ae60953cb1bbd53967b96eda432358` | `tests/tools/lck/test_state.py` | restored/adapted | Moved under the isolated LCK test root and updated for package imports/current v2 contracts. |
| `tests/tools/test_lck_structure.py` | `432f0a366faf4226b9177d90f1927ae03c879696` | `tests/tools/lck/test_structure.py` | restored/adapted | Updated from the historical facade plus nested `lck_core` shape to the single flat `tools/lck` package while preserving responsibility and dependency guards. |
| `tests/tools/test_lck_typed_dependency_contracts.py` | `9d3246e2b41aa8716b45b0ef0d3c918c6ca4c84e` | `tests/tools/lck/test_typed_dependency_contracts.py` | restored/adapted | Moved under the isolated LCK test root and updated for package imports/current v2 contracts. |
| `tests/tools/test_markdown_sections.py` | `2bfdb0df36945e11a5c89d6356cc0e26c0e37a45` | `tests/tools/lck/test_markdown_sections.py` | restored/adapted | Moved under the isolated LCK test root and updated for package imports/current v2 contracts. |
| `tests/tools/test_pr_resolve.py` | `279b8fb5353c02b7f80c251f597e3c8b6e3698ed` | `tests/tools/lck/test_pr_resolve.py` | restored/adapted | Moved under the isolated LCK test root and updated for package imports/current v2 contracts. |
| `tests/tools/test_retrieval_v2_policy.py` | `d80eed775a0f22b32b225f7aac4339ebf187649a` | `tests/tools/lck/test_retrieval_v2_policy.py` | restored/adapted | Retrieval semantics moved out of mixed product instructions into `.agents/policies/context-retrieval.md`; tests now guard that isolated owner. |
| `tests/tools/test_runtime_telemetry_removed.py` | `a7fbf857f3530f2469609ed9ec0544a74cc88fba` | `tests/tools/lck/test_runtime_telemetry_removed.py` | restored/adapted | Reduced historical path allowlists to direct guards against a runtime telemetry module/command and a check of the isolated external-analysis policy. |
| `tests/tools/test_workflow_common.py` | `161b0031afc517b38f7496a003b5b6a76c96f21a` | `tests/tools/lck/test_workflow_common.py` | restored/adapted | Moved under the isolated LCK test root and updated for package imports/current v2 contracts. |
| `tests/tools/test_workflow_evidence.py` | `cccdebb06afaa66bca463d2fa4f1f455b17c1fab` | `tests/tools/lck/test_feature_audit.py` | restored/adapted | Moved under the isolated LCK test root and updated for package imports/current v2 contracts. |
| `tests/tools/test_workflow_skills.py` | `a3000a8f7348aa8fa07a2c5c1e8f0d4336f1525c` | `tests/tools/lck/test_workflow_skills.py` | restored/adapted | Updated for the module CLI and one-canonical-source design; lifecycle authority, read-only Review, manual merge, and adapter routing remain covered. |
| `tests/tools/test_workflow_validation.py` | `1998975563ab92d7a7a43aad95ccb66a7725bd5c` | `tests/tools/lck/test_validation_runner.py` | restored/adapted | Moved under the isolated LCK test root and updated for package imports/current v2 contracts. |
| `tests/tools/test_wsl2_validation_rules.py` | `ab72dbd7f6466a811209545c3d6c9bb34a595164` | `tests/tools/lck/test_wsl2_validation_rules.py` | restored/adapted | Moved under the isolated LCK test root and updated for package imports/current v2 contracts. |
| `tests/tools/test_wsl2_validation_runner.py` | `4a3beaf77f170c67c263befe2c8498e3d51eace6` | `tests/tools/lck/test_wsl2_validation_runner.py` | restored/adapted | Moved under the isolated LCK test root and updated for package imports/current v2 contracts. |
| `.agents/execution-profile.example.toml` | `3540fda7da639a24b5aeb5d6c203fa1f5024c3a4` | `.agents/execution-profile.example.toml` | restored/adapted | Paths and module entry commands adapted. |
| `.agents/policies/command-execution.md` | `f0d891214027fbbc8a561d8e070a0f9c0bc7cc1a` | `.agents/policies/command-execution.md` | restored/adapted | Paths and module entry commands adapted. |
| `.agents/policies/workflow-evidence.md` | `02cc896af843aaec5060619b278bd78e2eb351d1` | `.agents/policies/workflow-evidence.md` | restored/adapted | Paths and module entry commands adapted. |
| `.agents/skills/feature-completion-audit/SKILL.md` | `9bf78f0c57e71f046b9c47c2abe2b94eb15b8175` | `.agents/skills/feature-completion-audit/SKILL.md` | restored/adapted | Paths and module entry commands adapted. |
| `.agents/skills/task-closeout/SKILL.md` | `5409c28b8d9f8fc20631b91f3c66e0e2e4a674c5` | `.agents/skills/task-closeout/SKILL.md` | restored/adapted | Paths and module entry commands adapted. |
| `.agents/skills/task-delivery-runner/SKILL.md` | `ac93fef57b7971d4fd8fed2945a8eb632d0e46f8` | `.agents/skills/task-delivery-runner/SKILL.md` | restored/adapted | Paths and module entry commands adapted. |
| `.agents/skills/task-pr-review-runner/SKILL.md` | `a590cb43408bef7af28b4fdc3b4255279b20706a` | `.agents/skills/task-pr-review-runner/SKILL.md` | restored/adapted | Paths and module entry commands adapted. |
| `.claude/settings.json` | `1b3f170fa76ff791bede19f43b64d7cf54dcc2ab` | `.claude/settings.json` | restored/adapted | Paths and module entry commands adapted. |
| `.claude/skills/feature-completion-audit/SKILL.md` | `534321b46cd4e3da8445c9c5d2e258da42594339` | `.claude/skills/feature-completion-audit/SKILL.md` | replaced by thin adapter | Canonical procedure is .agents/skills; provider copy is intentionally not duplicated. |
| `.claude/skills/task-closeout/SKILL.md` | `082e681e1e5e655c8e979599cfe29882b755d764` | `.claude/skills/task-closeout/SKILL.md` | replaced by thin adapter | Canonical procedure is .agents/skills; provider copy is intentionally not duplicated. |
| `.claude/skills/task-delivery-runner/SKILL.md` | `1e7d95550fcc41cb69147a6a09140d515891c7ff` | `.claude/skills/task-delivery-runner/SKILL.md` | replaced by thin adapter | Canonical procedure is .agents/skills; provider copy is intentionally not duplicated. |
| `.claude/skills/task-pr-review-runner/SKILL.md` | `2dbf4473b0bfcb77057df9da5a7fea4e6b7ca0e4` | `.claude/skills/task-pr-review-runner/SKILL.md` | replaced by thin adapter | Canonical procedure is .agents/skills; provider copy is intentionally not duplicated. |
| `.codex/rules/tracequant-wsl-validation.rules` | `e0dfd5e852687870fd23e2d39b55d9c330194440` | `.codex/rules/tracequant-wsl-validation.rules` | restored/adapted | Paths and module entry commands adapted. |
| `docs/guides/LCK-overview.md` | `787acf1aeeb55cb18ceacb4c77789255f427aaec` | `docs/guides/lck/overview.md` | restored/adapted | Moved into an LCK-only documentation subtree and updated for current paths. |
| `docs/guides/LCK-adoption.md` | `899c3ad89016db499801de17606dac44de384828` | `docs/guides/lck/adoption.md` | restored/adapted | Moved into an LCK-only documentation subtree and updated for current paths. |
| `docs/workflows/LCK-v1-Design-Charter.md` | `ef1f23d95d13b44b22ba821977613c5f2f26e6f4` | `docs/workflows/lck/design-charter-v1.md` | restored/adapted | Moved into an LCK-only documentation subtree and updated for current paths. |
| `docs/workflows/agent-skills.md` | `155549b4b2d317f0b616f75fe4811446438af305` | `docs/workflows/lck/agent-skills.md` | restored/adapted | Moved into an LCK-only documentation subtree and updated for current paths. |
| `docs/workflows/wsl2-validation-runner/README.md` | `672a5c35559d6ec69c678bb8ccfcedc7402805e0` | `docs/workflows/lck/validation/README.md` | restored/adapted | Moved into an LCK-only documentation subtree and updated for current paths. |
| `docs/workflows/wsl2-validation-runner/maintenance-and-adoption.md` | `ca7ad760f25205dbc4e170d29d9fd51c327f2d62` | `docs/workflows/lck/validation/maintenance-and-adoption.md` | restored/adapted | Moved into an LCK-only documentation subtree and updated for current paths. |
| `docs/workflows/wsl2-validation-runner/security-hardening-cases.md` | `2ae2b88608eeb0e1ebd1a9bbad27a30c141e7088` | `docs/workflows/lck/validation/security-hardening-cases.md` | restored/adapted | Moved into an LCK-only documentation subtree and updated for current paths. |
| `docs/development/issue-authoring.md` | `0869f8e57fc4138b2fcbb3c0ad1428da47e0eab4` | `docs/workflows/lck/issue-contracts.md` | restored/adapted | Moved into an LCK-only documentation subtree and updated for current paths. |
| `docs/development/issue-workflow.md` | `18161bdfd081dbec8f16e83078ce635fa31ea792` | `docs/workflows/lck/lifecycle.md` | restored/adapted | Moved into an LCK-only documentation subtree and updated for current paths. |
| `docs/development/pr-review.md` | `424aa29fe7dee740766ab4a5ef8908c879c1bcc9` | `docs/workflows/lck/review-and-remediation.md` | restored/adapted | Moved into an LCK-only documentation subtree and updated for current paths. |
| `docs/architecture/typed-leaf-workflows.md` | `b114439330ad53e368f8249d0016b03c7bec7fea` | `docs/workflows/lck/typed-profiles.md` | restored/adapted | Moved into an LCK-only documentation subtree and updated for current paths. |
| `AGENTS.md` | `3ef45f8e74f1d4b5c25acae810bfa69d49d6c2c7` | `AGENTS.md` | current v2 file adapted | Historical mixed product/workflow instructions were not overlaid; LCK routing stays concise at root. |
| `AGENTS.md (LCK context-retrieval sections)` | `3ef45f8e74f1d4b5c25acae810bfa69d49d6c2c7` | `.agents/policies/context-retrieval.md` | responsibility extracted | Leaf-first, progressive retrieval and external telemetry boundaries moved into an LCK-only policy owner. |
| `CLAUDE.md` | `7c1efae9ce826356ee4d5ce3e937fe7cd0af1447` | `CLAUDE.md` | current v2 file adapted | Historical file was not overlaid; only its still-applicable LCK responsibility was merged. |
| `.gitattributes` | `aad11276a1a2152bf9feca13cd0537c9a9f7f62d` | `.gitattributes` | current v2 file adapted | Historical file was not overlaid; only its still-applicable LCK responsibility was merged. |
| `.gitignore` | `a625b693be15d9d387678af02fb0194f4d4ec399` | `.gitignore` | current v2 file adapted | Historical file was not overlaid; only its still-applicable LCK responsibility was merged. |
| `.github/workflows/ci.yml` | `2a19a3721dd45b7ec1d2dcf4d22e8ae0b46b45b3` | `.github/workflows/ci.yml` | current v2 file adapted | Historical file was not overlaid; only its still-applicable LCK responsibility was merged. |
| `README.md` | `21eb4e755667e2cd6ae9350230c2c3557c7d841f` | `README.md` | restored/adapted | Restored the complete historical LCK introduction, motivation, capability, lifecycle, and adoption section; updated paths and distinguished the Task #333 source from immutable preview releases. |
| `pyproject.toml` | `d9f954bfce4cc4ffa038c9e25445e7336921f8b1` | `pyproject.toml` | current v2 file adapted | Historical file was not overlaid; only its still-applicable LCK responsibility was merged. |
| `uv.toml` | `90c772803bfaf728c71656d065260ffcf951e4f6` | `uv.toml` | current v2 file adapted | Historical file was not overlaid; only its still-applicable LCK responsibility was merged. |
| `docs/architecture/repository-structure.md` | `47868bda04b51328173369b31f48876e08fcc182` | `docs/architecture/repository-structure.md` | current v2 file adapted | Historical file was not overlaid; only its still-applicable LCK responsibility was merged. |

## Deliberately excluded v1 trees

The archived product implementations under `src/tracequant/data/`,
`src/tracequant/config.py`, `src/tracequant/logging.py`,
`src/tracequant/core/`, and the old `apps/`, `packages/`, and `deploy/`
trees do not provide an LCK responsibility and are not restored. Product,
research-data, release-history, and planning artifacts that merely mention LCK
also remain archived; current product documentation links to the isolated LCK
documentation instead.

## Canonical-source rule

Normative workflow documents live only under `docs/workflows/lck/`; public
usage guidance lives only under `docs/guides/lck/`; executable canonical
Skills live only under `.agents/skills/`. Claude Skill files are thin
provider adapters and contain no second lifecycle procedure.
