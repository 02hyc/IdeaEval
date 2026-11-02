Your task is to create a detailed summary of the conversation so far, paying close attention to the user's explicit requests and your previous actions.
This summary should be thorough in capturing technical details, research insights, file operations, and decisions that would be essential for continuing your research work without losing context.

**CRITICAL — Preserve the user's original research request verbatim:**
If the conversation contains the user's original research prompt (usually inside `<user_prompt>` tags in the first notification), you MUST include it **word-for-word** in your summary under a clearly labeled section called "Original User Research Request". Do NOT paraphrase, shorten, or omit it. This is the ground truth that all work must align with, and losing it causes the agent to drift off-topic after compaction.

Before providing your final summary, use the following process to organize your thoughts and ensure you've covered all necessary points. In your analysis process:

1. Chronologically analyze each message and section of the conversation. For each section thoroughly identify:
   - The research tasks you were working on
   - Your approach to addressing research questions
   - Key decisions about research directions and knowledge base organization
   - Specific details like:
     - File paths (paper summaries, research topics, research directions)
     - Research questions raised and their status
     - Papers reviewed and key findings
     - Knowledge base modifications
   - Errors or issues you encountered and how you resolved them
   - Important feedback or corrections from the system

2. Double-check for accuracy and completeness, addressing each required element thoroughly.

Your summary should include the following sections:

0. Original User Research Request: Copy the user's original research prompt **verbatim** (the content inside `<user_prompt>` tags from the first notification). Wrap it in `<user_prompt>` tags in your summary. If you cannot find the exact text, reproduce it as faithfully as possible and note that it may be approximate.

1. Primary Research Tasks: Capture all research tasks you were working on, including paper exploration, knowledge base building, and research question formulation.

2. Key Research Concepts: List important research topics, methodologies, and technical concepts you've been exploring.

3. Files and Knowledge Base:
   - Paper summaries reviewed (with file paths)
   - Research topics created or modified (with file paths)
   - Research directions updated
   - Topic-level durable thinking artifacts (`wonder_log.md`, `claim_audit.md`, `synthesis.md`) and whether key claims were raw-paper-verified
   - Any other files created, read, or modified
   - Include a brief summary of why each file operation was important

4. Research Questions: Document research questions you've raised, their context, and current status (resolved, pending, or under investigation).

5. Errors and Fixes: List any issues encountered (missing papers, malformed files, tool errors) and how you resolved them.

6. Problem Solving: Document problems solved during your research exploration and any ongoing investigations.

7. Pending Tasks: Outline any pending research tasks from your todo list that you have been explicitly working on.

8. Current Work: Describe in detail precisely what you were working on immediately before this summary, including:
   - The specific research task or question
   - Files you were examining or modifying
   - Your line of reasoning or investigation

9. Next Steps: List the next research steps you should take, ensuring they align with your ongoing research goals and todo list. Include specific details about what to investigate next.

CONVERSATION TO SUMMARIZE:
{conversation}

Please provide your summary following the above structure, ensuring precision and thoroughness. Focus on preserving the research context and knowledge base state so you can seamlessly continue your work.
