# Research notes: decision classifiers for agent tool calls, and Mozilla's principles

Compiled November 2026 from web searches. Every claim links to the page it came from. Content from sources has been paraphrased for compliance with licensing restrictions. Where something could not be confirmed, it says so.

## (a) Guard and decision classifiers

**Anthropic Constitutional Classifiers (2025).** Input and output classifiers trained on synthetic data generated from a natural-language "constitution" of what is allowed and what is not. The first generation cut jailbreak success from 86% to 4.4% on an unguarded model, at a cost of 23.7% more compute and a 0.38% rise in refusals of harmless queries ([Anthropic](https://www.anthropic.com/research/next-generation-constitutional-classifiers); [paper, arXiv 2501.18837](https://arxiv.org/html/2501.18837v1)). The idea we borrow: write the rules in plain language first, then generate training data from them.

**Constitutional Classifiers++ (2026).** A two-stage design: a cheap probe on the model's internal activations screens all traffic and passes suspicious exchanges to a stronger classifier that reads both sides of the conversation. About 1% extra compute and a lower refusal rate. Anthropic names two attack types that still get through: reconstruction attacks (harmful content split into harmless-looking pieces) and output obfuscation ([Anthropic](https://www.anthropic.com/research/next-generation-constitutional-classifiers); [arXiv 2601.04603](https://arxiv.org/html/2601.04603)). The cheap-screen-then-escalate pattern matches our design: a fast linear model, with ESCALATE handing hard cases to people.

**Meta Llama Prompt Guard 2.** Two small classifiers (86M and 22M parameters) that flag prompt injection and jailbreak text. The 86M model was trained on English and non-English attacks; the 22M model is about 75% cheaper but weaker on multilingual tasks ([Meta model card](https://dev.meta.ai/llama/docs/model-cards-and-prompt-formats/prompt-guard); [Llama Protections](https://dev.meta.ai/llama/llama-protections)). A 2026 study reports AUROC 0.757 (86M) and 0.714 (22M) on its injection benchmark, below an LLM judge at 0.897, and argues for calibrated deferral to humans ([arXiv 2610.20907](https://arxiv.org/html/2610.20907)).

**IBM Granite Guardian.** A family of judge models for prompt and response risks, including jailbreaks, bias, RAG groundedness, and hallucinated function calls in agent workflows ([paper, arXiv 2412.07724](https://arxiv.org/pdf/2412.07724)). Granite Guardian 4.1 8B (April 2026) adds "bring your own criteria" so users can define their own judging rules ([IBM docs](https://www.ibm.com/granite/docs/models/guardian); [GitHub](https://github.com/ibm-granite/granite-guardian)). User-defined criteria are close in spirit to community-written LOKAHI manifests.

**Google ShieldGemma.** Open-weight safety classifiers built on Gemma 2 for text (sexual content, dangerous content, harassment, hate speech), and ShieldGemma 2, a 4B image classifier on Gemma 3 ([arXiv 2407.21772](https://arxiv.org/html/2407.21772v1); [model card](https://ai.google.dev/gemma/docs/shieldgemma/model_card_2)). These are content-harm classifiers. They do not decide about tool calls or actions.

**Horizon-Labs "agent-i-o-guards".** Found on Hugging Face: a collection of small open multilingual models (Apache-2.0, ONNX), including a prompt-injection guard for direct and indirect injection in emails, web pages, RAG chunks and tool outputs, a groundedness checker and a PII and secrets redactor covering 29 entity types ([collection](https://huggingface.co/collections/Horizon-Labs/agent-i-o-guards); [prompt-injection-guard-small](https://huggingface.co/Horizon-Labs/prompt-injection-guard-small); [pii-redactor-base](https://huggingface.co/Horizon-Labs/pii-redactor-base)). We have not tested these models. They are candidate upstream feature extractors (an "injection score" and "PII present" flag) for a later version.

**AgentDojo (ETH Zurich, NeurIPS 2024).** A benchmark environment for agents that run tools over untrusted data. It measures both utility and security under prompt injection, and found that capable models fail many tasks even without attacks ([arXiv 2406.13352](https://arxiv.org/abs/2406.13352); [GitHub](https://github.com/ethz-spylab/agentdojo)). It is the obvious external benchmark for a later version of this work.

**CaMeL (Google DeepMind and others, 2025).** Puts a protective system layer around the LLM: trusted control flow is separated from untrusted data, and capability-based policies are enforced at runtime. It solved 77% of AgentDojo tasks with provable security, against 84% for an undefended system ([arXiv 2503.18813](https://arxiv.org/abs/2503.18813); [PDF](http://css.csail.mit.edu/6.566/2026/readings/camel.pdf)). A 2026 survey notes that the field has converged on deterministic policy outside the model rather than training models to refuse ([arXiv 2606.26479](https://arxiv.org/html/2606.26479v1)). This supports our ordering: LOKAHI's deterministic rules first, the learned model only as an extra, stricter layer.

### Self-improving guard classifiers

- **Synthetic data from a constitution, refreshed by red-teaming.** Constitutional Classifiers are retrained on new synthetic data as red teams find attacks; humans write and revise the constitution ([Anthropic](https://www.anthropic.com/research/constitutional-classifiers)).
- **Feedback-and-retrain loops with end users.** Document classifiers in business settings are often maintained through an ongoing loop where users flag errors and a team retrains ([arXiv 2004.13152](https://arxiv.org/html/2004.13152v1)).
- **Active learning and deferral.** Asking humans about the most uncertain cases saves labelling effort, and calibrated deferral sends uncertain cases to people ([arXiv 2610.20907](https://arxiv.org/html/2610.20907)).

### What is safe

Learning loops are attack surfaces. Clean-label backdoor attacks on active learning reached up to 94% success with 0.5–1% poisoned data while staying invisible to human annotators ([arXiv 2508.05681](https://arxiv.org/html/2508.05681)). Continual learners can be steered by poisoned updates, which erode earlier tasks ([arXiv 2211.15875](https://arxiv.org/pdf/2211.15875); [arXiv 2311.10919](https://arxiv.org/pdf/2311.10919v1.pdf)). Our safe pattern follows from this:

1. Only human-reviewed labels become training data, with the reviewer recorded.
2. Never train on the model's own outputs or on agent-supplied feedback.
3. Every retrain is a new numbered version that has to pass an eval gate, including a fixed hand-written hard set and a false-ALLOW check that must not get worse.
4. People approve each release. Nothing updates itself in place, and rollback means loading the previous version.

## (b) Mozilla Foundation

**Mission.** Mozilla's summary portfolio strategy (Nov 2025) gives the north star as guarding the open nature of the internet, and the aspiration as "doing for AI what we did for the web" through a portfolio of mission-driven nonprofits and companies ([PDF](https://blog.mozilla.org/wp-content/blogs.dir/278/files/2025/11/Mozilla-Summary-Portfolio-Strategy.pdf); [blog](https://blog.mozilla.org/mozilla/rewiring-mozilla-ai-and-web/)). State of Mozilla 2025 says it wants AI defined by openness, agency and choice ([State of Mozilla](https://stateof.mozilla.org/)), run on a "double bottom line" of public interest and sustainable business ([blog](https://blog.mozilla.org/en/mozilla/state-of-mozilla-25-26/)). The Mozilla wiki strategy page describes a tech ecosystem where power is decentralised and AI is built with human agency at its core ([wiki](https://wiki.mozilla.org/Strategy)).

**Trustworthy AI principles.** Mozilla's trustworthy AI white paper names agency, accountability, privacy, fairness and safety as guiding principles ([executive summary](https://www.mozillafoundation.org/en/insights/trustworthy-ai-whitepaper/executive-summary/)). Its theory of change says AI should be designed with personal agency in mind, with privacy, transparency and well-being as key concerns, and companies should answer for discriminatory decisions, data abuse or unsafe systems ([theory of change](https://www.mozillafoundation.org/en/insights/trustworthy-ai-whitepaper/path-forward/introduction-theory-change/)). A 2024 update revisits the vision ([Accelerating Progress Toward Trustworthy AI](https://foundation.mozilla.org/en/research/library/accelerating-progress-toward-trustworthy-ai/whitepaper/)).

**Open source AI.** Mozilla published an open source AI strategy in 2026, framing open source and open standards as the way to decentralise control, as with the web ([blog](https://blog.mozilla.org/en/mozilla/mozilla-open-source-ai-strategy/)).

**"Human-Scale AI".** This phrase is real. It names the Mozilla Foundation Incubator's next cohort: bespoke, fit-for-purpose AI sized to the problem, possibly smaller, local, lower-compute, and under community control. It prefers working technology over ideas and points to places big systems overlook, including languages they don't speak ([Incubator page](https://www.mozillafoundation.org/en/what-we-do/grantmaking/incubator/human-scale-ai/); [call for proposals](https://www.mozillafoundation.org/en/what-we-do/grantmaking/incubator/human-scale-ai/call-for-proposals/); [FAQ](https://www.mozillafoundation.org/ca/what-we-do/grantmaking/incubator/human-scale-ai/call-for-proposals/faqs/)). A Johns Hopkins funding list gives the deadline as Nov. 20 ([JHU](https://ai.jhu.edu/data-science-and-ai-funding-opportunities/external-funding-opportunities/)). Check this date on Mozilla's own form ([form](https://mozillafoundation.tfaforms.net/f/humanscaleai)).

**2026–28 strategy.** Not found under that name. The newest strategy documents found are the Nov 2025 portfolio strategy and State of Mozilla 2025 (linked above).

### Principles shared by Mozilla's co-funders

Mozilla Foundation is a founding member of **Humanity AI**, a $500M philanthropic commitment co-chaired by Omidyar Network and the MacArthur Foundation, with Doris Duke, Ford, Lumina, Kapor, Mellon, Packard and Siegel Family Endowment ([Omidyar](https://omidyar.com/update/humanity-ai/); [MacArthur](https://www.macfound.org/press/press-releases/humanity-ai-commits-500-million-to-build-a-people-centered-future-for-ai)). Its stated aim is to give people a strong voice in AI decisions that usually leave them out, and it announced $18M in pooled grants in May 2026 ([Omidyar](https://omidyar.com/update/new-grants-to-shape-ai/)). Mozilla also co-anchors Artists Make Technology with Doris Duke, Hewlett and MacArthur ([Mozilla](https://www.mozillafoundation.org/en/artists-make-technology/)), and joined an earlier public-interest AI coalition with Packard, Democracy Fund, Ford, Heising-Simons, MacArthur, Kapor, Omidyar, Open Society and Wallace Global Fund ([Mozilla](https://www.mozillafoundation.org/fr/blog/mozilla-joins-philanthropic-coalition-vice-president-harris-to-support-public-interest-ai/)). These sources show co-funding relationships. They do not show who funds Mozilla Foundation itself. Across these groups the shared themes are people-centred AI, public interest over concentrated power, and civil society having a say.

## (c) Implications for our design

- **Write the rules first, then generate data from them.** CHARTER.md plays the part of the constitution, and the seed data is generated and validated against it, as Constitutional Classifiers do.
- **Deterministic rules outside the model come first.** LOKAHI and plain-code charter rules (irreversible, timed-out, residency) decide before the model, following CaMeL and the 2026 survey. The model can only make a decision stricter.
- **A cheap screen with deferral to people.** A linear model runs in well under a millisecond on CPU, and ESCALATE plays the part of the "stronger classifier", which here is a human.
- **Measure over-refusal as well as false-ALLOW.** Anthropic reports both. We report the false-ALLOW rate (target 0) and the false-REFUSE rate on benign cases, in every language.
- **Multilingual from day one.** Prompt Guard 2 22M loses accuracy on multilingual inputs, and Mozilla's Human-Scale AI call points at languages big systems don't speak. We test per language.
- **Human-reviewed learning only, versioned, with an eval gate.** Poisoning research shows why unsupervised loops are unsafe (DDM-13).
- **Fit Human-Scale AI.** A small, local, CPU-only model with rules written by the community, rather than a large general system.
- **Later:** add a Horizon-Labs injection or PII guard as an input feature, and evaluate on AgentDojo.

## Sources

- https://www.anthropic.com/research/constitutional-classifiers
- https://www.anthropic.com/research/next-generation-constitutional-classifiers
- https://arxiv.org/html/2501.18837v1
- https://arxiv.org/html/2601.04603
- https://dev.meta.ai/llama/docs/model-cards-and-prompt-formats/prompt-guard
- https://dev.meta.ai/llama/llama-protections
- https://arxiv.org/html/2610.20907
- https://arxiv.org/pdf/2412.07724
- https://www.ibm.com/granite/docs/models/guardian
- https://github.com/ibm-granite/granite-guardian
- https://arxiv.org/html/2407.21772v1
- https://ai.google.dev/gemma/docs/shieldgemma/model_card_2
- https://huggingface.co/collections/Horizon-Labs/agent-i-o-guards
- https://huggingface.co/Horizon-Labs/prompt-injection-guard-small
- https://huggingface.co/Horizon-Labs/pii-redactor-base
- https://arxiv.org/abs/2406.13352
- https://github.com/ethz-spylab/agentdojo
- https://arxiv.org/abs/2503.18813
- http://css.csail.mit.edu/6.566/2026/readings/camel.pdf
- https://arxiv.org/html/2606.26479v1
- https://arxiv.org/html/2004.13152v1
- https://arxiv.org/html/2508.05681
- https://arxiv.org/pdf/2211.15875
- https://arxiv.org/pdf/2311.10919v1.pdf
- https://blog.mozilla.org/wp-content/blogs.dir/278/files/2025/11/Mozilla-Summary-Portfolio-Strategy.pdf
- https://blog.mozilla.org/mozilla/rewiring-mozilla-ai-and-web/
- https://stateof.mozilla.org/
- https://blog.mozilla.org/en/mozilla/state-of-mozilla-25-26/
- https://wiki.mozilla.org/Strategy
- https://www.mozillafoundation.org/en/insights/trustworthy-ai-whitepaper/executive-summary/
- https://www.mozillafoundation.org/en/insights/trustworthy-ai-whitepaper/path-forward/introduction-theory-change/
- https://foundation.mozilla.org/en/research/library/accelerating-progress-toward-trustworthy-ai/whitepaper/
- https://blog.mozilla.org/en/mozilla/mozilla-open-source-ai-strategy/
- https://www.mozillafoundation.org/en/what-we-do/grantmaking/incubator/human-scale-ai/
- https://www.mozillafoundation.org/en/what-we-do/grantmaking/incubator/human-scale-ai/call-for-proposals/
- https://www.mozillafoundation.org/ca/what-we-do/grantmaking/incubator/human-scale-ai/call-for-proposals/faqs/
- https://ai.jhu.edu/data-science-and-ai-funding-opportunities/external-funding-opportunities/
- https://mozillafoundation.tfaforms.net/f/humanscaleai
- https://omidyar.com/update/humanity-ai/
- https://www.macfound.org/press/press-releases/humanity-ai-commits-500-million-to-build-a-people-centered-future-for-ai
- https://omidyar.com/update/new-grants-to-shape-ai/
- https://www.mozillafoundation.org/en/artists-make-technology/
- https://www.mozillafoundation.org/fr/blog/mozilla-joins-philanthropic-coalition-vice-president-harris-to-support-public-interest-ai/
