"""On-demand Training fiche generator.

The training feature lets the user explore a topic by drilling down
through wiki notes. A click on a dead wikilink under the configured
training folder calls into ``expand_concept()``, which:

  1. Reads the breadcrumb (parent fiche + theme index) for context.
  2. Calls the LLM (model + capabilities pinned via ``llm.tasks.training``)
     with the training system prompt and a structured ask.
  3. Lets the LLM optionally call ``image.generate`` for illustrations
     when a visual is genuinely useful (concept metaphors, anatomy,
     visual subjects). Mermaid + LaTeX are inlined directly in the
     markdown — no tool needed for those.
  4. Writes the produced fiche into the vault via the git guard.

The fiche convention (frontmatter, sections, "À explorer" links) lives
in the system prompt so the user can tweak it without code changes.
"""

from .service import (
    TrainingExpandError,
    TrainingExpandResult,
    expand_concept,
)

__all__ = [
    "TrainingExpandError",
    "TrainingExpandResult",
    "expand_concept",
]
