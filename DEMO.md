# Truce two-minute demo

1. Start from a fresh run with `npm run referee:reset`, then start the managed Slack listener.
2. In the shared thread, Alex sends `@Truce report: prepare the client update`. Show the draft card, exact metric changes, owner, input, and review controls.
3. Sam sends `@Truce demo unsafe-cleanup`. Point out the explicit “Injected cleanup proposal for testing” label.
4. Show the referee decision: `data/source_metrics.csv` is blocked, `working/report_input.csv` is waiting on Alex's report, and `scratch/debug.log` is ready for Sam's approval.
5. Have Alex attempt Sam's cleanup approval and show the rejection. Have Sam click `Clean eligible file`; show the verified quarantine receipt and unchanged source hash.
6. Alex clicks `Publish report`; show the server-generated report receipt.
7. Show the new deferred recheck. The working input has a new revision and requires Sam's fresh approval. Click it and show the actual quarantine outcome.
8. Finish with `npm run referee:status` and the saved report/quarantine evidence.

Never describe the injected candidate selection as model behavior, and never call a pending review an executed action.
