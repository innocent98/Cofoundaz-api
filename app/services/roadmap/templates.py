from typing import Any

ROADMAP_TEMPLATE_VERSION = 1

STAGE_TEMPLATES: dict[str, Any] = {
    "idea": {
        "key": "stage.idea",
        "phases": [
            {
                "name": "Shape the idea",
                "start_week": 0,
                "end_week": 4,
                "milestones": [
                    {
                        "title": "Write your problem statement",
                        "due_week": 1,
                        "tasks": [
                            {"title": "Describe the problem in one paragraph", "effort": "small"},
                            {"title": "List who has this problem", "effort": "small"},
                        ],
                    },
                    {
                        "title": "Sketch the solution",
                        "due_week": 3,
                        "tasks": [{"title": "Draft a one-page concept", "effort": "medium"}],
                    },
                ],
            },
            {
                "name": "First signals",
                "start_week": 4,
                "end_week": 8,
                "milestones": [
                    {
                        "title": "Talk to 5 potential users",
                        "due_week": 6,
                        "tasks": [
                            {"title": "Recruit 5 interviewees", "effort": "medium"},
                            {"title": "Run and note the interviews", "effort": "medium"},
                        ],
                    }
                ],
            },
        ],
    },
    "validation": {
        "key": "stage.validation",
        "phases": [
            {
                "name": "Validation",
                "start_week": 0,
                "end_week": 6,
                "milestones": [
                    {
                        "title": "Validate demand",
                        "due_week": 2,
                        "tasks": [
                            {"title": "Run 10 customer interviews", "effort": "medium"},
                            {"title": "Synthesize problem hypotheses", "effort": "small"},
                        ],
                    },
                    {
                        "title": "Pricing test",
                        "due_week": 5,
                        "tasks": [
                            {"title": "Draft 3 pricing options", "effort": "small"},
                            {"title": "Run a willingness-to-pay test", "effort": "medium"},
                        ],
                    },
                ],
            },
            {
                "name": "Build MVP",
                "start_week": 6,
                "end_week": 14,
                "milestones": [
                    {
                        "title": "Core flow",
                        "due_week": 10,
                        "tasks": [
                            {"title": "Define the core user journey", "effort": "medium"},
                            {"title": "Build the happy path", "effort": "large"},
                        ],
                    }
                ],
            },
        ],
    },
    "build": {
        "key": "stage.build",
        "phases": [
            {
                "name": "Build MVP",
                "start_week": 0,
                "end_week": 10,
                "milestones": [
                    {
                        "title": "Ship the MVP",
                        "due_week": 8,
                        "tasks": [
                            {"title": "Finish the core feature set", "effort": "large"},
                            {"title": "Set up analytics", "effort": "small"},
                        ],
                    }
                ],
            },
            {
                "name": "Closed beta",
                "start_week": 10,
                "end_week": 16,
                "milestones": [
                    {
                        "title": "Run a closed beta",
                        "due_week": 14,
                        "tasks": [
                            {"title": "Recruit 20 beta users", "effort": "medium"},
                            {"title": "Collect and triage feedback", "effort": "medium"},
                        ],
                    }
                ],
            },
        ],
    },
    "launch": {
        "key": "stage.launch",
        "phases": [
            {
                "name": "Launch prep",
                "start_week": 0,
                "end_week": 4,
                "milestones": [
                    {
                        "title": "Prepare go-to-market",
                        "due_week": 3,
                        "tasks": [
                            {"title": "Write launch messaging", "effort": "medium"},
                            {"title": "Line up launch channels", "effort": "medium"},
                        ],
                    }
                ],
            },
            {
                "name": "Public launch",
                "start_week": 4,
                "end_week": 8,
                "milestones": [
                    {
                        "title": "Public launch",
                        "due_week": 6,
                        "tasks": [
                            {"title": "Ship the public release", "effort": "large"},
                            {"title": "Monitor and respond to issues", "effort": "medium"},
                        ],
                    }
                ],
            },
        ],
    },
    "growth": {
        "key": "stage.growth",
        "phases": [
            {
                "name": "Acquisition",
                "start_week": 0,
                "end_week": 8,
                "milestones": [
                    {
                        "title": "Find a repeatable channel",
                        "due_week": 6,
                        "tasks": [
                            {"title": "Test 3 acquisition channels", "effort": "large"},
                            {"title": "Double down on the best one", "effort": "medium"},
                        ],
                    }
                ],
            },
            {
                "name": "Retention",
                "start_week": 8,
                "end_week": 16,
                "milestones": [
                    {
                        "title": "Improve retention",
                        "due_week": 12,
                        "tasks": [
                            {"title": "Instrument the activation funnel", "effort": "medium"},
                            {"title": "Ship one retention improvement", "effort": "large"},
                        ],
                    }
                ],
            },
        ],
    },
    "scale": {
        "key": "stage.scale",
        "phases": [
            {
                "name": "Scale operations",
                "start_week": 0,
                "end_week": 12,
                "milestones": [
                    {
                        "title": "Harden the org",
                        "due_week": 8,
                        "tasks": [
                            {"title": "Document core processes", "effort": "medium"},
                            {"title": "Hire against the plan", "effort": "large"},
                        ],
                    }
                ],
            },
            {
                "name": "Expand",
                "start_week": 12,
                "end_week": 24,
                "milestones": [
                    {
                        "title": "Open a new segment or market",
                        "due_week": 20,
                        "tasks": [
                            {"title": "Validate the new segment", "effort": "large"},
                            {"title": "Adapt positioning", "effort": "medium"},
                        ],
                    }
                ],
            },
        ],
    },
}
