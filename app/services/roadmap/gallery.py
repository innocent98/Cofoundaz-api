GALLERY_TEMPLATE_VERSION = 1

GALLERY_TEMPLATES = {
    "validation-sprint": {
        "id": "validation-sprint",
        "title": "Validation sprint",
        "stage": "validation",
        "category": "Fintech",
        "phases": [
            {
                "name": "Validation sprint",
                "start_week": 0,
                "end_week": 4,
                "milestones": [
                    {
                        "title": "Problem interviews",
                        "due_week": 1,
                        "tasks": [
                            {"title": "Recruit 10 target users", "effort": "medium"},
                            {"title": "Run and synthesize interviews", "effort": "medium"},
                        ],
                    },
                    {
                        "title": "Solution test",
                        "due_week": 3,
                        "tasks": [{"title": "Build a concierge test", "effort": "large"}],
                    },
                ],
            }
        ],
    },
    "mvp-build": {
        "id": "mvp-build",
        "title": "MVP build",
        "stage": "build",
        "category": "Fintech",
        "phases": [
            {
                "name": "MVP",
                "start_week": 0,
                "end_week": 8,
                "milestones": [
                    {
                        "title": "Core flow shipped",
                        "due_week": 6,
                        "tasks": [
                            {"title": "Build the core feature", "effort": "large"},
                            {"title": "Instrument analytics", "effort": "small"},
                        ],
                    }
                ],
            }
        ],
    },
    "go-to-market": {
        "id": "go-to-market",
        "title": "Go-to-market",
        "stage": "launch",
        "category": "B2C",
        "phases": [
            {
                "name": "Go-to-market",
                "start_week": 0,
                "end_week": 6,
                "milestones": [
                    {
                        "title": "Launch plan",
                        "due_week": 2,
                        "tasks": [
                            {"title": "Define positioning + channels", "effort": "medium"},
                            {"title": "Prepare launch assets", "effort": "medium"},
                        ],
                    }
                ],
            }
        ],
    },
    "pre-seed-raise": {
        "id": "pre-seed-raise",
        "title": "Pre-seed raise",
        "stage": None,
        "category": "General",
        "phases": [
            {
                "name": "Fundraise",
                "start_week": 0,
                "end_week": 10,
                "milestones": [
                    {
                        "title": "Deck + data room ready",
                        "due_week": 3,
                        "tasks": [
                            {"title": "Write the pitch deck", "effort": "large"},
                            {"title": "Assemble the data room", "effort": "medium"},
                        ],
                    },
                    {
                        "title": "Investor outreach",
                        "due_week": 8,
                        "tasks": [
                            {"title": "Build the investor list", "effort": "medium"},
                            {"title": "Run outreach + meetings", "effort": "large"},
                        ],
                    },
                ],
            }
        ],
    },
    "company-formation": {
        "id": "company-formation",
        "title": "Company formation",
        "stage": None,
        "category": "Nigeria",
        "phases": [
            {
                "name": "Incorporation",
                "start_week": 0,
                "end_week": 4,
                "milestones": [
                    {
                        "title": "Register the company",
                        "due_week": 2,
                        "tasks": [
                            {"title": "Reserve the company name (CAC)", "effort": "small"},
                            {"title": "File incorporation documents", "effort": "medium"},
                        ],
                    },
                    {
                        "title": "Tax + banking set up",
                        "due_week": 4,
                        "tasks": [
                            {"title": "Obtain TIN", "effort": "small"},
                            {"title": "Open a corporate bank account", "effort": "medium"},
                        ],
                    },
                ],
            }
        ],
    },
    "scale-playbook": {
        "id": "scale-playbook",
        "title": "Scale playbook",
        "stage": "scale",
        "category": "General",
        "phases": [
            {
                "name": "Scale",
                "start_week": 0,
                "end_week": 12,
                "milestones": [
                    {
                        "title": "Repeatable growth engine",
                        "due_week": 8,
                        "tasks": [
                            {"title": "Document the growth motion", "effort": "medium"},
                            {"title": "Hire against the plan", "effort": "large"},
                        ],
                    }
                ],
            }
        ],
    },
}


def template_counts(tmpl: dict) -> tuple[int, int]:
    milestones = [m for ph in tmpl["phases"] for m in ph["milestones"]]
    tasks = [t for m in milestones for t in m["tasks"]]
    return len(milestones), len(tasks)
