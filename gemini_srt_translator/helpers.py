from typing import List, Optional

from google.genai import types

# ==============================================================================
# INSTRUCTION GENERATION FOR TRANSLATION
# ==============================================================================


def is_polish(language: Optional[str]) -> bool:
    """Whether the target language is Polish (name or code, any case)."""
    return (language or "").strip().lower() in ("polish", "polski", "pl", "pol")


def get_translation_style_rules(language: str, section_number: int) -> str:
    """
    The subtitling style rules: condensing, the reading budget, what may and may not be
    dropped, natural target-language wording. Examples are added for Polish only.
    """
    polish = is_polish(language)

    def ex(text: str) -> str:
        return f" ({text})" if polish else ""

    rules = f"""
## {section_number}. Translation Style: Film Subtitles, Not a Transcript
A subtitle gives **the meaning, intent and tone** of a line, not every word: the viewer reads it while watching. A good {language} subtitle is usually clearly shorter than a word-for-word translation.

- **Reading budget**: each item has `max_chars` - how many characters a viewer can read in the subtitle's time (spaces count, tags don't, both lines together). Over it: condense. Well under it: keep the full meaning, don't cut for the sake of it. `max_chars` is input only - never return it.
- **Meaning has priority over the budget**: drop fillers, repetitions and names first, but when the only shorter wording loses or distorts what is said, keep the meaning and go slightly over the limit.
- **Drop or shorten** when the scene doesn't need it: filler (*well, you know, I mean, like, okay, look*), hesitation sounds (*uh, um*: a `...` shows the pause), stutters and false starts, repetitions{ex('*"No, no, no!"* → *"Nie!"*')}, subject pronouns the language doesn't need{ex('*"Nie wiem"*, not *"Ja nie wiem"*')}, names and forms of address once it's clear who talks to whom, tag questions (*right?, okay?*) that add nothing, long-winded wording{ex('*"Do you think you could possibly help me?"* → *"Pomożesz mi?"*')}.
- **Never drop**: information the story needs (facts, names on first mention, numbers, times, places), the subject or key noun the next line reacts to, negations and conditions, the point of a joke, irony, threats and promises, the character's register and emotion.
- **Numbers**: never drop a number to fit the budget; use digits if words are too long.
- **Sentences split across two items**: each part stays in its own item, and both parts must read as one grammatical {language} sentence together - never drop the verb from both.
- **Natural {language}, not a calque**: use the idiom a native speaker would use{ex('*"It&#39;s raining cats and dogs"* → *"Leje jak z cebra"*, *"I&#39;m on it"* → *"Już się robi"*')}. Wordplay that can't be kept: a joke with the same function. Unknown cultural references: a general equivalent.
- **Units**: metric when the exact number doesn't matter; keep numbers that matter for the plot or a joke. Money keeps its currency.
- **Swearing and crude slang**: the same strength and register, never stronger, and never sanitized or euphemized.
- **Echoes and callbacks**: when a speaker repeats or questions a word from the previous line, both lines use the same {language} word. A recurring quote is worded the same every time.
- **Short follow-up questions** (*"Of what?"*, *"For what?"*, *"Like what?"*): read them together with the previous line and repeat the noun or use the natural question{ex('*"I need a photo." - "Of what?"* → *"Zdjęcie czego?"*, not *"Czego?"*; *"Like what?"* → *"Na przykład?"*; *"For what?"* → *"Po co?"*')}.
- **Gender and formality**: keep the grammatical gender of the speaker and the listener and the level of formality between two characters consistent{ex('*ty* / *pan*, *pani*')}.
- **Layout**: at most 2 lines of up to 42 characters, broken at phrase boundaries or after punctuation. Two speakers in one item: each on their own line starting with `- `.
""".replace("&#39;", "'")
    return rules


def get_translate_instruction(
    language: str,
    thinking: bool,
    thinking_compatible: bool,
    audio_file: Optional[str] = None,
    description: Optional[str] = None,
) -> str:
    """
    Generates a structured instruction prompt for a subtitle translation task.

    Args:
        language: The target language for the translation (e.g., "French").
        thinking: If True, instructs the model to reason step-by-step.
        thinking_compatible: If True, the "Reasoning Protocol" section is included.
        audio_file: The name of an audio file provided for context.
        description: Additional user-provided instructions for context.

    Returns:
        The complete, formatted instruction string for the AI translation task.
    """
    prompt_parts = []
    section_number = 1

    # --- Section 1: Persona and Primary Goal ---
    prompt_parts.append(
        f"# INSTRUCTION: Translate Subtitles to {language}\n\n"
        "You are an expert AI linguist specializing in subtitle translation. Your goal is to translate the `text` field of each item into "
        f"**{language}**."
    )

    # --- Section 2: Input/Output Structure ---
    section_number += 1
    audio_structure = '"time_start": "01:56",\n    "time_end": "02:00",'
    json_structure = f"""
```json
[
  {{
    "index": "1",
    "text": "This is the first subtitle line.\\nThis is the second line.",
{'    ' + audio_structure if audio_file else '...'}
  }}
]
```"""
    prompt_parts.append(
        f"## {section_number}. Data Structure\nYou will receive and must return a list of items with this exact structure:\n{json_structure}\n"
        "Input items may also carry `max_chars` (the reading budget, see the style rules). Return only `index` and `text`."
    )

    # --- Section 3: Core Translation Rules ---
    section_number += 1
    core_rules = f"""
## {section_number}. Core Translation Rules
- **Translate Text Only**: Only translate the value of the `text` field.
- **Preserve Formatting**: Keep all existing formatting, including HTML tags (`<i>`, `<b>`) and line breaks (`\\n`). A line break is always `\\n` - never write ` / ` between lines or speakers.
- **Dashes**: Use a plain hyphen `-` for dialogue and pauses, never `—` or `–`.
- **Handle Empty Text**: If a `text` field is empty or contains only whitespace, keep it unchanged.
- **Maintain Integrity**:
  - Number of items in the output must match the input.
  - **Do NOT** alter any fields other than `text`.
  - **Do NOT** add, remove, or reorder any items on the list.
  - **Do NOT** merge text between different items. Original and translation must match.
- **Context-only lines**: A message may end with lines marked "CONTEXT ONLY" (the subtitles that follow the batch). Use them only to understand sentences that continue past the last item; never translate or return them.
"""
    prompt_parts.append(core_rules)

    # --- Translation style: film subtitles, not a transcript ---
    section_number += 1
    prompt_parts.append(get_translation_style_rules(language, section_number))

    # --- Section 4: Advanced Rules (Conditional) ---
    if audio_file:
        section_number += 1
        audio_rules = f"""
## {section_number}. Advanced Rules: Grammatical Gender from Audio
If the text is ambiguous, use the provided audio to infer the correct grammatical gender and formality.
- **Analyze Speaker's Voice**: Use the `time_start` and `time_end` (`MM:SS` format) to listen to the speaker.
  - If the speaker sounds **male**, use masculine grammatical forms.
  - If the speaker sounds **female**, use feminine forms.
  - **Example (French):** "I am tired." -> `Je suis fatigué` (male) vs. `Je suis fatiguée` (female).
- **Analyze Context**: If necessary, listen to the audio before or after the current segment to determine who is being addressed and apply the correct gender and pluralization.
  - **Example (Spanish):** "You are tired." -> `Ustedes están cansados` (male/mixed) vs. `Ustedes están cansadas` (female).
"""
        prompt_parts.append(audio_rules)

    # --- Section 5: User Context (Conditional) ---
    if description:
        section_number += 1
        user_description_section = f"""
## {section_number}. Additional User-Provided Context
Use this context to improve translation accuracy. These notes do not override core rules.
- {description}
"""
        prompt_parts.append(user_description_section)

    # --- Section 6: Reasoning Protocol (Conditional) ---
    if thinking_compatible:
        section_number += 1
        reasoning_instruction = (
            "**Think step-by-step**: Before generating the final JSON, reason through your choices to ensure all rules are strictly followed."
            if thinking
            else "**Respond directly**: Generate the JSON output immediately without providing a step-by-step reasoning process."
        )
        reasoning_protocol_section = f"""
## {section_number}. Reasoning Protocol
{reasoning_instruction}"""
        prompt_parts.append(reasoning_protocol_section)

    return "\n---\n".join(prompt_parts).strip()


# ==============================================================================
# INSTRUCTION GENERATION FOR TRANSCRIPTION
# ==============================================================================


def get_transcribe_instruction(thinking: bool, thinking_compatible: bool, description: Optional[str] = None) -> str:
    """
    Generates a complete instruction prompt for an AI audio transcription task.

    Args:
        thinking: If True, instructs the model to reason step-by-step.
        thinking_compatible: If True, the "Reasoning Protocol" section is included.
        description: Additional user-provided instructions for context.

    Returns:
        The complete, formatted instruction string for the AI transcription task.
    """
    prompt_parts = []
    section_number = 0

    # --- Section 1: Persona and Primary Goal ---
    prompt_parts.append(
        "# INSTRUCTION: Transcribe Audio to Subtitles\n\n"
        "You are an expert AI transcriber specializing in broadcast-quality subtitles. Your goal is to transcribe audio into a timed JSON array of subtitle objects."
    )

    # --- Section 2: Output Structure ---
    section_number += 1
    output_structure = f"""
## {section_number}. Output Structure
The output must be a JSON array. Each object must contain `text`, `time_start`, and `time_end`.
```json
{{
  "text": "This is the first line of the subtitle.\\nThis is the second line.",
  "time_start": "00:06",
  "time_end": "00:09"
}}
```"""
    prompt_parts.append(output_structure)

    # --- Section 3: Core Rules ---
    section_number += 1
    core_rules = f"""
## {section_number}. Core Formatting Rules
- **Timing**: Subtitles must be between **1-5 seconds** in duration.
- **Content**: Max **42 characters** per line, max **2 lines** per subtitle (use `\\n`).
- **Line Breaking**: Break lines logically (e.g., after punctuation, before conjunctions). Do not separate closely related words (e.g., article and noun).
- **Omit Non-Verbal Sounds**: Do not transcribe sounds like `[coughs]` or `[laughs]`. Use italics for song lyrics as described below."""
    prompt_parts.append(core_rules)

    # --- Section 4: Dialogue and Continuity ---
    section_number += 1
    dialogue_rules = f"""
## {section_number}. Dialogue and Continuity
- **Multiple Speakers**: Use a hyphen: `- Are you coming?\\n- In a minute.`
- **Interruptions**: Use two hyphens: `- What are you--\\n- Be quiet!`
- **Significant Pauses (2+ sec)**: End the preceding subtitle with an ellipsis (`...`).
- **Starting Mid-Sentence**: Begin the subtitle with an ellipsis (`...like this.`)."""
    prompt_parts.append(dialogue_rules)

    # --- Section 5: Text and Number Formatting ---
    section_number += 1
    formatting_rules = f"""
## {section_number}. Text and Number Formatting
- **Numbers**: Spell out numbers zero through ten; use digits for 11 and up. Always spell out a number that begins a sentence.
- **Dates & Times**: Transcribe as spoken but omit "the" and "of" (e.g., `March 6th`). Use numerals for specific times (`9:30 a.m.`).
- **Quotes**: Use double quotes (`"`) for dialogue and single quotes (`'`) for quotes within quotes. Punctuation goes *inside* the closing quote."""
    prompt_parts.append(formatting_rules)

    # --- Section 6: Italics and Songs ---
    section_number += 1
    italics_rules = f"""
## {section_number}. Italics and Songs
Use `<i></i>` tags **only** for:
- Narration, off-screen voices, or inner thoughts.
- Dialogue from electronic media (phones, TVs).
- Sung lyrics.
- Titles of books, movies, etc.
- Unfamiliar foreign words.
- Clear vocal emphasis (`It <i>was</i> you.`).

For songs, italicize lyrics and wrap the subtitle with music notes: `♪ <i>lyrics go here</i> ♪`."""
    prompt_parts.append(italics_rules)

    # --- Section 7: User Context (Conditional) ---
    if description:
        section_number += 1
        user_description_section = f"""
## {section_number}. Additional User-Provided Context
Use this context to improve transcription accuracy. These notes do not override core rules.
- {description}
"""
        prompt_parts.append(user_description_section)

    # --- Section 8: Reasoning Protocol (Conditional) ---
    if thinking_compatible:
        section_number += 1
        reasoning_instruction = (
            "**Think step-by-step**: Before generating the final JSON, reason through your choices to ensure all rules are strictly followed."
            if thinking
            else "**Respond directly**: Generate the JSON output immediately without providing a step-by-step reasoning process."
        )
        reasoning_protocol_section = f"""
## {section_number}. Reasoning Protocol
{reasoning_instruction}"""
        prompt_parts.append(reasoning_protocol_section)

    return "\n---\n".join(prompt_parts).strip()


# ==============================================================================
# API CONFIGURATION
# ==============================================================================


def get_safety_settings() -> List[types.SafetySetting]:
    """
    Configures the safety settings for the generative model.

    This function disables all safety filters (Hate Speech, Dangerous Content, etc.).
    This is necessary for tasks like subtitle translation or transcription of media,
    which may contain sensitive content that should be faithfully processed, not blocked.
    Use with caution and ensure compliance with content policies.

    Returns:
        A list of SafetySetting objects with all thresholds set to BLOCK_NONE.
    """
    return [
        types.SafetySetting(
            category=category,
            threshold=types.HarmBlockThreshold.BLOCK_NONE,
        )
        for category in [
            types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
            types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
            types.HarmCategory.HARM_CATEGORY_HARASSMENT,
            types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
        ]
    ]


def get_translate_response_schema() -> types.Schema:
    """
    Defines the expected JSON schema for the translation model's response.
    """
    return types.Schema(
        type="ARRAY",
        items=types.Schema(
            type="OBJECT",
            properties={
                "index": types.Schema(type="STRING"),
                "text": types.Schema(type="STRING"),
            },
            required=["index", "text"],
        ),
    )


def get_transcribe_response_schema() -> types.Schema:
    """
    Defines the expected JSON schema for the transcription model's response.
    """
    return types.Schema(
        type="ARRAY",
        items=types.Schema(
            type="OBJECT",
            properties={
                "text": types.Schema(type="STRING"),
                "time_start": types.Schema(type="STRING"),
                "time_end": types.Schema(type="STRING"),
            },
            required=["text", "time_start", "time_end"],
        ),
    )


# ==============================================================================
# EXAMPLE USAGE
# ==============================================================================

if __name__ == "__main__":
    print("----------- TRANSLATE INSTRUCTION (THINKING ENABLED) -----------")
    translate_instruction = get_translate_instruction(
        language="French",
        description="The setting is a formal business meeting. Please use formal address.",
        thinking=True,
        thinking_compatible=True,
        audio_file="meeting_audio.mp3",
    )
    print(translate_instruction)

    print("\n\n----------- TRANSCRIBE INSTRUCTION (THINKING DISABLED) -----------")
    transcribe_instruction = get_transcribe_instruction(
        thinking=False,
        thinking_compatible=True,
        description="The main speaker has a slight lisp.",
    )
    print(transcribe_instruction)

    print("\n\n----------- TRANSCRIBE INSTRUCTION (REASONING NOT COMPATIBLE) -----------")
    transcribe_instruction_no_reason = get_transcribe_instruction(
        thinking=True,  # This argument is ignored
        thinking_compatible=False,
    )
    print(transcribe_instruction_no_reason)
