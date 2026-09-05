import ollama

from app.core.config import get_settings
from app.utils.logger import get_logger

logger = get_logger(__name__)
settings = get_settings()


class NoteGenerationError(Exception):
    """Raised when note generation fails."""
    pass


class NoteGenerationService:
    def __init__(self):
        self.client = ollama.Client(host=settings.ollama_base_url)
        self.model = settings.ollama_model

    def generate_notes(self, transcript: str, title: str = "Video") -> str:
        """Generate structured markdown notes from a transcript."""
        prompt = f"""You are an expert educational AI tutor. 

FIRST, analyze the video transcript and classify it. If the video is NOT educational (e.g., entertainment, music, vlogs, random chatter), you MUST refuse to process it by returning ONLY this exact message:
"This video does not appear to contain educational content. I can only generate notes for coding, math, or theoretical videos."

If the video IS educational, create complete, high-quality study notes based on this video transcript.
Mix the content from the video transcript WITH your own expert knowledge and useful insights to provide a richer, more educational experience.

Video Title: {title}

Transcript:
{transcript}

IMPORTANT DOMAIN-SPECIFIC RULES:
- For Coding/Programming Videos: You MUST include code-related content. If the video's code has errors or is incomplete, provide the corrected and complete code.
- For Math Videos: You MUST provide step-by-step mathematical solutions and explanations. Use proper LaTeX notation ($...$ for inline, $$...$$ for block). Convert spoken math into formulas.
- For Theory Videos: Provide deep theoretical explanations, good theoretical context, and clarify any abstract concepts with strong examples.

Return clean markdown with exactly these sections:

# {title} - Comprehensive Study Notes

## AI Topic Analysis & Context
Write a brief overview explaining the broader importance of the topic and provide extra context or background knowledge not explicitly mentioned in the video.

## Summary (Mixed Content)
Write 2-3 concise paragraphs covering the main idea and key arguments. Combine the transcript's points with your own expert understanding.

## Key Points & Expert Additions
List 8-12 important takeaways. For each point, include what the video said PLUS a brief added insight, example, or clarification from your own knowledge. Bold the core concept.

## Domain-Specific Content (Code / Math Solutions / Theory)
Depending on the video type:
- If Coding: Provide the corrected, well-documented code examples discussed.
- If Math: Provide the step-by-step mathematical solutions using LaTeX.
- If Theory: Provide a deep-dive into the theoretical concepts and frameworks discussed.

## Chapter Breakdown
Break the topic into logical chapters/topics. Use ### chapter headings and 2-3 bullets per chapter. 

## Action Items & Further Learning
List practical next steps, revision tasks, or recommended concepts to explore further."""

        try:
            response = self.client.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a helpful assistant that creates excellent study notes from video transcripts."},
                    {"role": "user", "content": prompt},
                ],
                options={"temperature": 0.3, "num_ctx": 4096},
            )

            notes = response["message"]["content"]
            logger.info("notes_generated", title=title, model=self.model)
            return notes
        except Exception as e:
            logger.error("note_generation_failed", title=title, error=str(e))
            raise NoteGenerationError(f"Note generation failed: {e}")
