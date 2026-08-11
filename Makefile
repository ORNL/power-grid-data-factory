.PHONY: frontier-exago frontier-exago-clean frontier-exago-dry-run

frontier-exago:
	./scripts/build_exago_frontier.sh

frontier-exago-clean:
	./scripts/build_exago_frontier.sh --clean

frontier-exago-dry-run:
	./scripts/build_exago_frontier.sh --dry-run