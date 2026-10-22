# Engineering Onboarding Guide

## Development Environment

Install the Northwind command line tool and run nw dev setup to create your local environment. The setup script installs the correct language runtimes and starts the local database in a container.

## Code Review

Every pull request to the main branch needs approval from two reviewers, and at least one of them must be a code owner of the changed files. Keep pull requests under 400 changed lines where possible; large changes are hard to review well.

## Deployments

We deploy to production from Monday to Thursday. Deployments are not allowed on Fridays after 14:00 or on public holidays, so that problems are not discovered over the weekend.

Every deployment goes to a canary that receives 5 percent of traffic for 30 minutes before the full rollout.

## On-call

New engineers join the on-call rotation after six weeks, once they have shadowed two shifts. On-call engineers receive a weekly allowance and an extra day off after a week with night pages.
