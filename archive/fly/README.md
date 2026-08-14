# Archived: Fly.io deploy assets

Superseded 2026-08-14 by the Railway deploy (see /DEPLOY.md). Kept for reference,
not deleted — these were the original take-home deploy path.

State at archive time: the Fly app `holly-demo-kg-0717` and its 3GB `holly_data`
volume exist in the personal org but were NEVER successfully deployed (no image, no
releases, volume never attached). Nothing on them to migrate. To clean up:

    fly apps destroy holly-demo-kg-0717   # removes the app AND its volume
