"""
Coachd Document Processor
Handles document ingestion, chunking, and text extraction
"""

import os
import re
from typing import List, Dict, Any, Optional
from pathlib import Path
from pypdf import PdfReader
from docx import Document as DocxDocument
from dataclasses import dataclass
import hashlib


@dataclass
class DocumentChunk:
    """Represents a chunk of text from a document"""
    content: str
    metadata: Dict[str, Any]
    chunk_id: str
    document_id: str


class DocumentProcessor:
    """Processes documents for ingestion into the vector database"""
    
    def __init__(self, chunk_size: int = 500, chunk_overlap: int = 50):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.supported_extensions = {'.pdf', '.docx', '.txt', '.md'}
    
    def extract_text_from_pdf(self, file_path: str) -> str:
        """Extract text from a PDF file"""
        reader = PdfReader(file_path)
        text_parts = []
        
        for page in reader.pages:
            text = page.extract_text()
            if text:
                text_parts.append(text)
        
        return "\n\n".join(text_parts)
    
    def extract_text_from_docx(self, file_path: str) -> str:
        """Extract text from a DOCX file"""
        doc = DocxDocument(file_path)
        text_parts = []
        
        for paragraph in doc.paragraphs:
            if paragraph.text.strip():
                text_parts.append(paragraph.text)
        
        return "\n\n".join(text_parts)
    
    def extract_text_from_txt(self, file_path: str) -> str:
        """Extract text from a plain text file"""
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()
    
    def extract_text(self, file_path: str) -> str:
        """Extract text from a file based on its extension"""
        ext = Path(file_path).suffix.lower()
        
        if ext == '.pdf':
            return self.extract_text_from_pdf(file_path)
        elif ext == '.docx':
            return self.extract_text_from_docx(file_path)
        elif ext in {'.txt', '.md'}:
            return self.extract_text_from_txt(file_path)
        else:
            raise ValueError(f"Unsupported file type: {ext}")
    
    def clean_text(self, text: str) -> str:
        """Clean and normalize text"""
        # Replace multiple whitespace with single space
        text = re.sub(r'\s+', ' ', text)
        # Remove leading/trailing whitespace
        text = text.strip()
        return text
    
    def chunk_text(self, text: str, document_id: str, metadata: Dict[str, Any]) -> List[DocumentChunk]:
        """Split text into overlapping chunks"""
        chunks = []
        text = self.clean_text(text)
        
        # Split by sentences for more natural chunks
        sentences = re.split(r'(?<=[.!?])\s+', text)
        
        current_chunk = ""
        chunk_index = 0
        
        for sentence in sentences:
            # If adding this sentence would exceed chunk size
            if len(current_chunk) + len(sentence) > self.chunk_size and current_chunk:
                # Save current chunk
                chunk_id = f"{document_id}_chunk_{chunk_index}"
                chunks.append(DocumentChunk(
                    content=current_chunk.strip(),
                    metadata={**metadata, "chunk_index": chunk_index},
                    chunk_id=chunk_id,
                    document_id=document_id
                ))
                
                # Start new chunk with overlap
                words = current_chunk.split()
                overlap_words = words[-self.chunk_overlap:] if len(words) > self.chunk_overlap else words
                current_chunk = " ".join(overlap_words) + " " + sentence
                chunk_index += 1
            else:
                current_chunk += " " + sentence if current_chunk else sentence
        
        # Don't forget the last chunk
        if current_chunk.strip():
            chunk_id = f"{document_id}_chunk_{chunk_index}"
            chunks.append(DocumentChunk(
                content=current_chunk.strip(),
                metadata={**metadata, "chunk_index": chunk_index},
                chunk_id=chunk_id,
                document_id=document_id
            ))
        
        return chunks
    
    def generate_document_id(self, file_path: str) -> str:
        """Generate a unique document ID based on file content"""
        with open(file_path, 'rb') as f:
            file_hash = hashlib.md5(f.read()).hexdigest()[:12]
        filename = Path(file_path).stem
        return f"{filename}_{file_hash}"
    
    def process_document(self, file_path: str, category: Optional[str] = None) -> List[DocumentChunk]:
        """Process a document and return chunks"""
        path = Path(file_path)
        
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        
        if path.suffix.lower() not in self.supported_extensions:
            raise ValueError(f"Unsupported file type: {path.suffix}")
        
        # Extract text
        text = self.extract_text(file_path)
        
        if not text.strip():
            raise ValueError(f"No text content extracted from: {file_path}")
        
        # Generate document ID
        document_id = self.generate_document_id(file_path)
        
        # Prepare metadata
        metadata = {
            "filename": path.name,
            "file_type": path.suffix.lower(),
            "category": category or "general",
            "source": file_path
        }
        
        # Chunk the text
        chunks = self.chunk_text(text, document_id, metadata)
        
        return chunks
    
    def process_directory(self, directory: str, category: Optional[str] = None) -> List[DocumentChunk]:
        """Process all supported documents in a directory"""
        all_chunks = []
        dir_path = Path(directory)
        
        if not dir_path.exists():
            raise FileNotFoundError(f"Directory not found: {directory}")
        
        for file_path in dir_path.iterdir():
            if file_path.suffix.lower() in self.supported_extensions:
                try:
                    chunks = self.process_document(str(file_path), category)
                    all_chunks.extend(chunks)
                    print(f"✓ Processed: {file_path.name} ({len(chunks)} chunks)")
                except Exception as e:
                    print(f"✗ Error processing {file_path.name}: {e}")
        
        return all_chunks
