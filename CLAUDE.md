cat > CLAUDE.md << 'EOF'
# Project rules

This is a learning portfolio project. The author is a student
building a foundation in data science. Optimize for clarity and
teachability, never for cleverness or brevity.

## Code level

Write code an intern would write and understand. Specifically:

ALLOWED
- pandas, numpy, matplotlib, seaborn, scikit-learn, sqlalchemy
- plain functions with clear names
- explicit for loops when they read better than vectorized tricks
- simple list comprehensions (one level, no nesting)
- f-strings

NOT ALLOWED unless I explicitly ask
- classes, decorators, generators, context managers
- lambda beyond a trivial one-liner
- nested comprehensions, chained method calls longer than 3 steps
- async, multiprocessing, threading
- advanced typing (Protocol, TypeVar, Generic)
- config frameworks, dependency injection, abstract base classes
- one-liners that pack several operations together

## Style

- One function does one thing. Under 20 lines.
- Comment every non-obvious line with what the function receives
  and what it returns.
- Prefer explicit and verbose over compact and clever.
- No premature abstraction. Repeat code before generalizing it.
- Variable names in full words: station_id, not sid.

## Workflow

- Explain the approach before writing code. Wait for confirmation.
- After writing, list the functions I need to be able to explain
  in an interview.
- Never introduce a library that is not already in requirements.txt
  without asking first.

## Language

All code, comments, commit messages and documentation in English.
EOF
