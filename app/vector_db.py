"""
Coachd Vector Database
ChromaDB integration with agency-scoped collections
"""

import chromadb
from chromadb import Settings as ChromaSettings
from typing import List, Dict, Any, Optional
from pathlib import Path

from .document_processor import DocumentChunk
from .config import settings


class VectorDatabase:
    """Manages document embeddings and semantic search using ChromaDB"""
    
    def __init__(self, persist_directory: Optional[str] = None):
        self.persist_directory = persist_directory or settings.chroma_persist_dir
        
        # Ensure directory exists
        Path(self.persist_directory).mkdir(parents=True, exist_ok=True)
        
        # Initialize ChromaDB with persistence
        self.client = chromadb.PersistentClient(
            path=self.persist_directory,
            settings=ChromaSettings(anonymized_telemetry=False)
        )
        
        print("✓ Vector database initialized")
    
    def _get_collection_name(self, agency: Optional[str] = None) -> str:
        """Get collection name for an agency"""
        if agency:
            # Normalize agency name for collection
            safe_name = agency.lower().replace(" ", "_").replace("-", "_")
            return f"agency_{safe_name}"
        return "coachd_shared"
    
    def _get_collection(self, agency: Optional[str] = None):
        """Get or create a collection for an agency"""
        collection_name = self._get_collection_name(agency)
        return self.client.get_or_create_collection(
            name=collection_name,
            metadata={"description": f"Coachd knowledge base for {agency or 'shared'}"}
        )
    
    def add_chunks(self, chunks: List[DocumentChunk], agency: Optional[str] = None) -> int:
        """Add document chunks to an agency's collection"""
        if not chunks:
            return 0
        
        collection = self._get_collection(agency)
        
        # Prepare data for ChromaDB
        ids = [chunk.chunk_id for chunk in chunks]
        documents = [chunk.content for chunk in chunks]
        metadatas = [
            {**chunk.metadata, "document_id": chunk.document_id}
            for chunk in chunks
        ]
        
        # Add to collection (ChromaDB handles embeddings automatically)
        collection.add(
            ids=ids,
            documents=documents,
            metadatas=metadatas
        )
        
        return len(chunks)
    
    def search(
        self, 
        query: str, 
        top_k: int = 5,
        category: Optional[str] = None,
        agency: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Search for relevant documents in an agency's collection"""
        
        collection = self._get_collection(agency)
        
        # Check if collection has documents
        if collection.count() == 0:
            return []
        
        # Build where clause for filtering
        where_clause = None
        if category:
            where_clause = {"category": category}
        
        # Search (ChromaDB handles query embedding automatically)
        results = collection.query(
            query_texts=[query],
            n_results=min(top_k, collection.count()),
            where=where_clause,
            include=["documents", "metadatas", "distances"]
        )
        
        # Format results
        formatted_results = []
        if results and results['ids'] and results['ids'][0]:
            for i, chunk_id in enumerate(results['ids'][0]):
                formatted_results.append({
                    "chunk_id": chunk_id,
                    "content": results['documents'][0][i],
                    "metadata": results['metadatas'][0][i],
                    "distance": results['distances'][0][i],
                    "relevance_score": 1 - results['distances'][0][i]
                })
        
        return formatted_results
    
    def get_document_count(self, agency: Optional[str] = None) -> int:
        """Get the total number of chunks in an agency's collection"""
        collection = self._get_collection(agency)
        return collection.count()
    
    def list_documents(self, agency: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all unique documents in an agency's collection"""
        collection = self._get_collection(agency)
        all_items = collection.get(include=["metadatas"])
        
        documents = {}
        for metadata in all_items.get('metadatas', []):
            doc_id = metadata.get('document_id', 'unknown')
            if doc_id not in documents:
                documents[doc_id] = {
                    "document_id": doc_id,
                    "filename": metadata.get('filename', 'unknown'),
                    "category": metadata.get('category', 'general'),
                    "file_type": metadata.get('file_type', 'unknown')
                }
        
        return list(documents.values())
    
    def delete_document(self, document_id: str, agency: Optional[str] = None) -> int:
        """Delete all chunks belonging to a document"""
        collection = self._get_collection(agency)
        
        results = collection.get(
            where={"document_id": document_id},
            include=["metadatas"]
        )
        
        if results and results['ids']:
            collection.delete(ids=results['ids'])
            return len(results['ids'])
        
        return 0
    
    def clear_agency(self, agency: str) -> int:
        """Clear all documents for an agency"""
        collection_name = self._get_collection_name(agency)
        
        try:
            collection = self.client.get_collection(collection_name)
            count = collection.count()
            self.client.delete_collection(collection_name)
            return count
        except Exception:
            return 0
    
    def list_agencies(self) -> List[str]:
        """List all agencies with collections"""
        collections = self.client.list_collections()
        agencies = []
        for col in collections:
            if col.name.startswith("agency_"):
                # Convert collection name back to agency name
                agency_name = col.name.replace("agency_", "").upper()
                agencies.append(agency_name)
        return agencies


# Singleton instance
_db_instance: Optional[VectorDatabase] = None


def get_vector_db() -> VectorDatabase:
    """Get the singleton vector database instance"""
    global _db_instance
    if _db_instance is None:
        _db_instance = VectorDatabase()
    return _db_instance
