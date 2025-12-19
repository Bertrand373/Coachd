"""
Coachd.ai - Winning Rebuttals Generator
==========================================
Auto-generates a master document of rebuttals that have led to closed deals.
This document gets indexed into ChromaDB and influences future AI guidance.

The document is:
- Auto-generated from call outcome data
- Protected from accidental deletion
- Admin-only access for manual edits
- Re-indexed weekly (or on-demand)

This is the feedback loop that makes the AI smarter over time.
"""

import os
import json
from datetime import datetime
from typing import Optional
from .call_outcomes import get_outcome_storage, WinningGuidance

# ==================== CONFIG ====================

# Use /data on Render, fallback to local
import os
if os.path.exists("/data"):
    SYSTEM_DOCS_PATH = "/data/system_docs"
else:
    SYSTEM_DOCS_PATH = "./system_docs"
    
WINNING_REBUTTALS_FILE = "WINNING_REBUTTALS.md"
METADATA_FILE = "winning_rebuttals_meta.json"
MIN_OUTCOMES_THRESHOLD = 20  # Don't generate until we have enough data
MIN_SUCCESS_COUNT = 2  # Minimum closes to include a rebuttal

# Objection type labels for the document
OBJECTION_LABELS = {
    "price": "Price / Can't Afford",
    "spouse": "Spouse / Need to Discuss",
    "think": "Want to Think About It",
    "timing": "Bad Timing",
    "coverage": "Already Has Coverage",
    "interest": "Not Interested",
    "trust": "Trust / Rapport Issues",
    "other": "Other Objections"
}


# ==================== GENERATOR ====================

class WinningRebuttalsGenerator:
    """
    Generates and manages the winning rebuttals master document.
    """
    
    def __init__(self):
        self.storage = get_outcome_storage()
        self.doc_path = os.path.join(SYSTEM_DOCS_PATH, WINNING_REBUTTALS_FILE)
        self.meta_path = os.path.join(SYSTEM_DOCS_PATH, METADATA_FILE)
        self._ensure_paths()
    
    def _ensure_paths(self):
        """Ensure system docs directory exists"""
        os.makedirs(SYSTEM_DOCS_PATH, exist_ok=True)
    
    def should_generate(self) -> tuple[bool, str]:
        """
        Check if we should generate/update the document.
        Returns (should_generate, reason)
        """
        outcome_count = self.storage.get_outcome_count()
        
        if outcome_count < MIN_OUTCOMES_THRESHOLD:
            return False, f"Need {MIN_OUTCOMES_THRESHOLD} outcomes, have {outcome_count}"
        
        winning = self.storage.get_winning_guidance(MIN_SUCCESS_COUNT)
        if not winning:
            return False, "No rebuttals have met the success threshold yet"
        
        return True, f"Ready: {outcome_count} outcomes, {len(winning)} winning rebuttals"
    
    def generate(self, force: bool = False) -> dict:
        """
        Generate the winning rebuttals document.
        
        Args:
            force: Generate even if threshold not met (for testing)
        
        Returns:
            Status dict with success, message, and stats
        """
        # Check if we should generate
        should, reason = self.should_generate()
        if not should and not force:
            return {
                "success": False,
                "message": reason,
                "generated": False
            }
        
        # Get winning guidance
        winning = self.storage.get_winning_guidance(MIN_SUCCESS_COUNT if not force else 1)
        
        if not winning:
            return {
                "success": False,
                "message": "No winning rebuttals found",
                "generated": False
            }
        
        # Get common objections for context
        objections = self.storage.get_common_objections()
        
        # Generate the document
        doc_content = self._build_document(winning, objections)
        
        # Write the document
        with open(self.doc_path, 'w') as f:
            f.write(doc_content)
        
        # Update metadata
        meta = {
            "last_updated": datetime.now().isoformat(),
            "rebuttal_count": len(winning),
            "total_outcomes": self.storage.get_outcome_count(),
            "version": self._get_next_version()
        }
        
        with open(self.meta_path, 'w') as f:
            json.dump(meta, f, indent=2)
        
        print(f"[WINNING] Generated document with {len(winning)} rebuttals")
        
        return {
            "success": True,
            "message": f"Generated with {len(winning)} winning rebuttals",
            "generated": True,
            "path": self.doc_path,
            "stats": {
                "rebuttal_count": len(winning),
                "top_success_rate": winning[0].success_rate if winning else 0
            }
        }
    
    def _build_document(self, winning: list[WinningGuidance], objections: list) -> str:
        """Build the markdown document content"""
        
        lines = [
            "# Proven Rebuttals That Close Deals",
            "",
            "> **SYSTEM DOCUMENT** - Auto-generated from real call outcomes.",
            "> These rebuttals have been proven to lead to closed deals.",
            "> Last updated: " + datetime.now().strftime("%B %d, %Y at %I:%M %p"),
            "",
            "---",
            "",
        ]
        
        # Top performers section
        lines.append("## Top Performing Rebuttals")
        lines.append("")
        lines.append("These phrases have the highest close rates when used:")
        lines.append("")
        
        for i, w in enumerate(winning[:10], 1):
            lines.append(f"### {i}. {w.success_rate}% Success Rate")
            lines.append("")
            lines.append(f"> \"{w.text}\"")
            lines.append("")
            lines.append(f"*Used {w.total_uses} times, led to {w.success_count} closes*")
            lines.append("")
        
        # Objection-specific section if we have data
        if objections:
            lines.append("---")
            lines.append("")
            lines.append("## Common Objections to Overcome")
            lines.append("")
            lines.append("These are the objections that most often kill deals. Focus training here:")
            lines.append("")
            
            for obj in objections[:5]:
                label = OBJECTION_LABELS.get(obj['objection'], obj['objection'])
                lines.append(f"- **{label}**: {obj['count']} lost deals ({obj['percentage']}%)")
            
            lines.append("")
        
        # Usage instructions
        lines.append("---")
        lines.append("")
        lines.append("## How to Use This Document")
        lines.append("")
        lines.append("1. **During calls**: The AI will automatically prioritize these proven rebuttals")
        lines.append("2. **For training**: Review top performers with new agents")
        lines.append("3. **For coaching**: Identify which objection types need more training material")
        lines.append("")
        lines.append("*This document updates automatically as more call data is collected.*")
        
        return "\n".join(lines)
    
    def _get_next_version(self) -> int:
        """Get next version number"""
        try:
            with open(self.meta_path, 'r') as f:
                meta = json.load(f)
                return meta.get('version', 0) + 1
        except:
            return 1
    
    def get_metadata(self) -> Optional[dict]:
        """Get current document metadata"""
        try:
            with open(self.meta_path, 'r') as f:
                return json.load(f)
        except:
            return None
    
    def get_document(self) -> Optional[str]:
        """Get current document content"""
        try:
            with open(self.doc_path, 'r') as f:
                return f.read()
        except:
            return None
    
    def document_exists(self) -> bool:
        """Check if document has been generated"""
        return os.path.exists(self.doc_path)


# ==================== CHROMADB INTEGRATION ====================

def reindex_winning_rebuttals(chroma_client, collection_name: str = "documents") -> dict:
    """
    Re-index the winning rebuttals document into ChromaDB.
    
    This should be called:
    1. After generating/updating the document
    2. On a weekly schedule
    3. Manually via admin endpoint
    
    Args:
        chroma_client: ChromaDB client instance
        collection_name: Name of the collection to index into
    
    Returns:
        Status dict
    """
    generator = WinningRebuttalsGenerator()
    
    if not generator.document_exists():
        return {
            "success": False,
            "message": "Winning rebuttals document does not exist yet"
        }
    
    try:
        content = generator.get_document()
        meta = generator.get_metadata()
        
        if not content:
            return {"success": False, "message": "Could not read document"}
        
        collection = chroma_client.get_or_create_collection(name=collection_name)
        
        # Use a fixed ID so we update rather than duplicate
        doc_id = "SYSTEM_WINNING_REBUTTALS"
        
        # Delete existing if present
        try:
            collection.delete(ids=[doc_id])
        except:
            pass
        
        # Add with special metadata
        collection.add(
            documents=[content],
            ids=[doc_id],
            metadatas=[{
                "source": "SYSTEM_WINNING_REBUTTALS",
                "type": "system_document",
                "generated": True,
                "version": meta.get('version', 1) if meta else 1,
                "last_updated": meta.get('last_updated', '') if meta else '',
                "priority": "high"  # Signal to RAG to weight this higher
            }]
        )
        
        print(f"[WINNING] Indexed into ChromaDB collection '{collection_name}'")
        
        return {
            "success": True,
            "message": "Document indexed successfully",
            "collection": collection_name,
            "version": meta.get('version', 1) if meta else 1
        }
        
    except Exception as e:
        print(f"[WINNING] Index error: {e}")
        return {
            "success": False,
            "message": f"Index error: {str(e)}"
        }


# ==================== SCHEDULED TASK ====================

def weekly_refresh(chroma_client) -> dict:
    """
    Run the full refresh: generate document and re-index.
    Call this from a scheduled task (e.g., every Sunday night).
    """
    generator = WinningRebuttalsGenerator()
    
    # Generate/update document
    gen_result = generator.generate()
    
    if not gen_result.get('success') and not gen_result.get('generated'):
        return gen_result
    
    # Re-index into ChromaDB
    index_result = reindex_winning_rebuttals(chroma_client)
    
    return {
        "success": index_result.get('success', False),
        "generation": gen_result,
        "indexing": index_result
    }


# ==================== SINGLETON ====================

_generator_instance = None

def get_generator() -> WinningRebuttalsGenerator:
    global _generator_instance
    if _generator_instance is None:
        _generator_instance = WinningRebuttalsGenerator()
    return _generator_instance
