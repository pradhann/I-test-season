"""Content intelligence: what FPL creators are saying, and who has earned a say.

The premise is that creators collectively hold information no statistical model
sees -- a manager's press-conference tone about a knock, which winger looked
sharp in a friendly, who took the pens in a pre-season game nobody has data for.
The premise is true. The trap is that the *aggregate* of that information is the
template, which this engine already models directly from ownership, so an
unweighted creator consensus adds a noisy duplicate of a feature it has.

This package therefore does two separable things:

1. Extracts structured, falsifiable claims -- ``(creator, player_code, action,
   gameweek, confidence, rationale, source_url, published_at)`` -- rather than
   summaries. A summary cannot be scored and therefore cannot earn a weight.
2. Scores every creator against their own past claims, and multiplies their
   opinion by a weight that is zero until they have demonstrated an edge.

See :mod:`fpl_edge.ingest.content.scoring` for the weighting argument and
``docs/content_sources.md`` for the measured reachability of every source.
"""

from fpl_edge.ingest.content.models import Action, Claim, ContentItem

__all__ = ["Action", "Claim", "ContentItem"]
