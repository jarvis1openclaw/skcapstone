SHELL := /bin/bash

.PHONY: bootstrap check clean-room-check dev-deps dev-deps-check dev-deps-down format-check lint \
	type-check unit-test integration-test migration-check sbom secret-scan \
	vulnerability-scan fixture-check license-audit license-audit-live

bootstrap:
	./scripts/bootstrap.sh

check:
	./scripts/bootstrap.sh
	./scripts/run_checks.sh all

clean-room-check:
	python3 ./scripts/clean_room_check.py

dev-deps:
	./scripts/dev_dependencies.sh up

dev-deps-check:
	./scripts/dev_dependencies.sh check

dev-deps-down:
	./scripts/dev_dependencies.sh down

license-audit-live:
	./scripts/bootstrap.sh
	./scripts/run_checks.sh license-audit-live

format-check lint type-check unit-test integration-test migration-check sbom secret-scan vulnerability-scan fixture-check license-audit:
	./scripts/bootstrap.sh
	./scripts/run_checks.sh $@
