# Listener-first product orientation

**Status: implemented 2026-07-12** (SKILL.md, podcast-principles.md,
transcript-quality-goal.md).

## Problem

The current episode guidance asks the host to establish what a product
"watches" and "says back" in a plain mechanical sentence. That framing
can cause the host to solicit implementation details before the listener
understands the user's situation or the consequence the product addresses.
The guest is also told never to pitch, but is not given a positive pattern
for explaining a product clearly without selling it.

As a result, an episode can pass the orientation lint while still making a
cold listener assemble the product's purpose from feature names and
mechanics.

## Desired behavior

Within the first four exchanges, the conversation should establish four
things in this order of understanding:

1. The situation the intended user is in.
2. What can go wrong in that situation.
3. What the product changes for the user.
4. How the product accomplishes that change in concrete terms.

The host may elicit these across two short exchanges. The first answer does
not need to carry a complete rehearsed pitch. The sequence should sound like
a conversation, while leaving a cold listener with a coherent problem-and-
solution model.

All claims remain source-grounded. If the dossier does not support a broad
market problem or urgency claim, the guest should describe the builder's own
situation honestly rather than inventing customer demand or "why now."

## Prompt changes

### Guest

Add a product-orientation rule: when asked what the product is, orient rather
than sell. Begin with the user's situation and the concrete consequence, then
explain the intervention and only the mechanics needed to make it tangible.
Avoid feature inventories, undefined product nouns, taglines used as
explanations, and unsupported market claims.

Clarify "never pitch" to mean no selling, exaggeration, or polished marketing
performance. It must not prevent a clear explanation of the product's value.

### Host

Replace the mechanics-first instruction and "what it watches / what it says
back" example. Ask first about the user's situation and what goes wrong, then
follow naturally with what the product changes and how. Permit this to span
two short exchanges within the existing four-exchange orientation window.

### Principles and evaluation

Update the opening principle and orientation lint to require a comprehensible
problem-and-solution model, not merely the presence of a mechanical sentence.
Extend the cold-listener review so the reviewer must be able to state:

- who is in the relevant situation;
- what goes wrong without the product;
- what the product changes; and
- how it does so concretely.

Add a feature-list introduction anti-pattern: mechanisms, internal nouns, or
taglines arrive before the listener understands the problem they serve.

## Scope

Change only:

- `skills/agent-fm/SKILL.md`
- `docs/podcast-principles.md`
- `docs/transcript-quality-goal.md`

Do not change `publish.py`, generated episode artifacts, or the source
transcript. This is an editorial-generation change, not a mechanical pipeline
change.

## Verification

- Search the three files for the old "what it watches, what it says back"
  framing and confirm it has been replaced consistently.
- Read the guest, host, principle, lint, and cold-listener instructions as one
  flow and confirm they do not contradict one another.
- Run the offline test suite to ensure documentation and skill edits have not
  disturbed repository behavior.
