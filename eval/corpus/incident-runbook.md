# Incident Response Runbook

## Severity Levels

SEV1 is a full outage of a core product or any confirmed loss of customer data. SEV2 is a major degradation that affects many customers but has a workaround. SEV3 is a minor issue that affects a small number of customers.

When in doubt, declare the higher severity; it is easy to downgrade later.

## Paging and Escalation

For a SEV1, page the primary on-call engineer immediately through PagerBell. The primary must acknowledge the page within 5 minutes. If the page is not acknowledged within 10 minutes, PagerBell escalates automatically to the secondary on-call and then to the engineering manager.

SEV2 incidents page the primary on-call during business hours only; outside business hours they create a high-priority ticket instead.

## Roles

Every SEV1 and SEV2 incident has an Incident Commander who coordinates the response and makes decisions, a Communications Lead who updates customers and stakeholders, and a Scribe who keeps a timeline in the incident channel.

The Incident Commander should not debug the problem themselves; their job is to coordinate.

## Customer Communication

For SEV1 incidents the status page must be updated within 15 minutes of the incident being declared, and then at least every 30 minutes until it is resolved.

## Postmortems

A written postmortem is required for every SEV1 and SEV2 incident and must be published within 5 business days. Postmortems are blameless: they focus on systems and processes, not on individuals.

Every postmortem lists action items with an owner and a due date, and they are tracked in the reliability backlog.
