import streamlit as st
import uuid
from datetime import datetime, time
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import re
import tempfile
import os
import asyncio
import PyPDF2
import fitz  # PyMuPDF
from mongodb_client import AtlasClient
from s3_file_manager import S3FileManager

# Initialize clients
@st.cache_resource
def get_clients():
    mongo_client = AtlasClient()
    s3_client = S3FileManager()
    return mongo_client, s3_client

mongo_client, s3_client = get_clients()

# Configuration
COLLECTION_NAME = "documents"
FLAGS_COLLECTION = "document_flags"
S3_FOLDER = "qu-agents/documents/"

# Initialize default flags
DEFAULT_FLAGS = ["Review", "Convert", "Use", "Ignore"]

def initialize_flags():
    """Initialize default flags in database if they don't exist"""
    try:
        existing_flags = mongo_client.find(FLAGS_COLLECTION)
        if not existing_flags:
            for flag in DEFAULT_FLAGS:
                mongo_client.insert(FLAGS_COLLECTION, {
                    "flag_name": flag,
                    "created_at": datetime.utcnow()
                })
    except Exception as e:
        st.error(f"Error initializing flags: {str(e)}")

def get_available_flags():
    """Get all available flags from database"""
    try:
        flags = mongo_client.find(FLAGS_COLLECTION)
        return [flag["flag_name"] for flag in flags]
    except Exception:
        return DEFAULT_FLAGS

def add_new_flag(flag_name):
    try:
        existing_flags = get_available_flags()
        lc = {f.lower() for f in existing_flags}
        if flag_name and flag_name.lower() not in lc:
            mongo_client.insert(FLAGS_COLLECTION, {
                "flag_name": flag_name,
                "created_at": datetime.utcnow()
            })
            return True
        return False
    except Exception as e:
        st.error(f"Error adding new flag: {str(e)}")
        return False

# --- Tag helpers ---
import re
def _init_tag_state():
    if "tags_list" not in st.session_state:
        st.session_state["tags_list"] = []
    if "tag_input" not in st.session_state:
        st.session_state["tag_input"] = ""

def _consume_tag_input_if_complete():
    """Convert trailing space/comma-delimited input into tags, then clear the input."""
    raw = st.session_state.get("tag_input", "")
    if not raw:
        return
    # Trigger conversion when the input ends with space or comma OR contains multiple tokens
    trigger = raw.endswith((" ", ",")) or re.search(r"[,\s]", raw)
    if not trigger:
        return
    tokens = [t.strip().lower() for t in re.split(r"[,\s]+", raw) if t.strip()]
    if tokens:
        # De-dupe while keeping order
        existing = set(st.session_state["tags_list"])
        for t in tokens:
            if t not in existing:
                st.session_state["tags_list"].append(t)
                existing.add(t)
        # Clear the input box so the user can type the next tag
        st.session_state["tag_input"] = ""
        st.rerun()

def main():
    st.set_page_config(
        page_title="Document Management System",
        page_icon="📚",
        layout="wide"
    )
    
    st.title("📚 Document Management System")
    
    # Initialize flags
    initialize_flags()
    
    # Sidebar navigation
    page = st.sidebar.selectbox(
        "Choose a page",
        ["Insert Document", "Search Documents"]
    )
    
    if page == "Insert Document":
        insert_page()
    elif page == "Search Documents":
        search_page()
    # elif page == "Dive Deeper":
    #     dive_deeper_page()
def insert_page():
    st.header("📝 Insert New Document")

    # --- Details ---
    st.subheader("Details")
    c1, c2 = st.columns([1.2, 1])
    with c1:
        name = st.text_input("Document Name*", placeholder="Enter document name", help="A short, unique title.")
        description = st.text_area("Description*", placeholder="Enter description", help="What this document is about.")
    with c2:
        tags_input = st.text_input("Tags", placeholder="e.g. invoice, 2025, onboarding")
        tags = [t.strip() for t in tags_input.split(",") if t.strip()]
        notes = st.text_area("Notes", placeholder="Any additional notes", help="Optional context.")

    # --- Flags (selector + contextual new-flag UI directly beneath) ---
    available_flags = get_available_flags()
    selected_flags = st.multiselect(
        "Select flags for this document",
        available_flags,
        help="Pick one or more existing flags."
    )

    # CONTEXTUAL: directly under the multiselect
    with st.popover("➕ Create a new flag", use_container_width=True,):
        new_flag_name = st.text_input("New flag name", placeholder="Enter new flag name", key="new_flag_name_insert")
        if st.button("Add Flag", key="add_flag_insert"):
            if not new_flag_name:
                st.warning("Please enter a flag name.")
            else:
                if add_new_flag(new_flag_name):
                    st.success(f"Flag '{new_flag_name}' added successfully!")
                    st.toast(f"Flag '{new_flag_name}' added")
                    st.rerun()  # refresh the multiselect choices instantly
                else:
                    st.info(f"Flag '{new_flag_name}' already exists.")

    # --- Files ---
    st.subheader("File Upload")
    uploaded_files = st.file_uploader(
        "Choose files",
        accept_multiple_files=True,
        type=['pdf', 'doc', 'docx', 'txt', 'jpg', 'jpeg', 'png', 'mp4', 'mp3'],
        help="You can upload multiple files."
    )
    if uploaded_files:
        st.caption("Selected files")
        for f in uploaded_files:
            st.write(f"• `{f.name}` — {f.size} bytes")

    # --- Submit ---
    can_submit = bool(name and description and uploaded_files)
    submit = st.button("Submit Document", type="primary", disabled=not can_submit)

    if submit:
        try:
            with st.status("Uploading document...", expanded=False) as status:
                # Generate unique document ID
                doc_id = str(uuid.uuid4())

                # Upload files to S3
                s3_files = []
                for file in uploaded_files:
                    file_key = f"{S3_FOLDER}{doc_id}/{file.name}"
                    with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
                        tmp_file.write(file.getvalue())
                        tmp_file.flush()
                        success = asyncio.run(s3_client.upload_file(tmp_file.name, file_key))
                        os.unlink(tmp_file.name)
                    if success:
                        s3_url = f"https://{s3_client.bucket_name}.s3.amazonaws.com/{file_key}"
                        s3_files.append({
                            "filename": file.name,
                            "s3_key": file_key,
                            "s3_url": s3_url,
                            "size": file.size,
                            "type": file.type
                        })
                        st.write(f"Uploaded: `{file.name}`")

                if not s3_files:
                    status.update(label="Failed", state="error")
                    st.error("Failed to upload files to S3!")
                    return

                # Create document record
                document = {
                    "doc_id": doc_id,
                    "name": name,
                    "description": description,
                    "tags": tags,
                    "notes": notes,
                    "flags": selected_flags,  # new flags get selected after rerun if desired
                    "files": s3_files,
                    "created_at": datetime.utcnow(),
                    "updated_at": datetime.utcnow()
                }

                # Insert into MongoDB
                result_id = mongo_client.insert(COLLECTION_NAME, document)

                if result_id:
                    status.update(label="Done", state="complete")
                    st.success(f"Document uploaded successfully! Document ID: {doc_id}")
                    st.balloons()
                    st.subheader("Uploaded Files")
                    for file_info in s3_files:
                        st.write(f"• {file_info['filename']} — {file_info['size']} bytes")
                else:
                    status.update(label="Failed", state="error")
                    st.error("Failed to save document to database!")

        except Exception as e:
            st.error(f"Error uploading document: {str(e)}")
            
    
def search_page():
    st.header("🔍 Search Documents")

    # ---------- Tag helpers for chip input (space/comma -> tag) ----------
    import re
    def _init_tag_state_search():
        if "tags_list_search" not in st.session_state:
            st.session_state["tags_list_search"] = []
        if "tag_input_search" not in st.session_state:
            st.session_state["tag_input_search"] = ""
    def _consume_tag_input_if_complete_search():
        raw = st.session_state.get("tag_input_search", "")
        if not raw:
            return
        # convert when user hits space/comma or types multiple tokens
        trigger = raw.endswith((" ", ",")) or re.search(r"[,\s]", raw)
        if not trigger:
            return
        tokens = [t.strip().lower() for t in re.split(r"[,\s]+", raw) if t.strip()]
        if tokens:
            existing = set(st.session_state["tags_list_search"])
            for t in tokens:
                if t not in existing:
                    st.session_state["tags_list_search"].append(t)
                    existing.add(t)
            st.session_state["tag_input_search"] = ""
            st.rerun()

    # ---------- Filters panel ----------
    with st.container():
        c1, c2 = st.columns([1.2, 1])
        with c1:
            search_query = st.text_input(
                "Search text",
                placeholder="Name, description, or notes…",
                help="Case-insensitive search across name, description, and notes.",
                key="search_text",
            )
        with c2:
            sort_by = st.selectbox(
                "Sort by",
                options=["Newest created", "Last updated", "Name (A→Z)"],
                index=0,
                help="Change result ordering.",
                key="sort_by",
            )

    # Tags chip input (space/comma turns token into a chip)
    st.markdown("##### Tags filter")
    _init_tag_state_search()
    st.text_input(
        "Add tags",
        key="tag_input_search",
        placeholder="Type a tag and press space (or comma)",
    )
    _consume_tag_input_if_complete_search()

    # Show tags as removable chips via multiselect
    current_tags = st.multiselect(
        "Current tags",
        options=st.session_state["tags_list_search"],
        default=st.session_state["tags_list_search"],
        help="Click × to remove a tag.",
        key="tags_display_search",
    )
    if set(current_tags) != set(st.session_state["tags_list_search"]):
        st.session_state["tags_list_search"] = current_tags

    # Flag filter
    available_flags = get_available_flags()
    flag_filter = st.multiselect(
        "Flag filter",
        options=available_flags,
        help="Filter results that have any of the selected flags.",
        key="flag_filter",
    )

    # Date range filter
    d1, d2, d3 = st.columns([1, 1, 1])
    with d1:
        use_dates = st.checkbox("Filter by date range", key="use_dates")
    with d2:
        start_date = st.date_input("Start (created on/after)", value=None, key="start_date") if use_dates else None
    with d3:
        end_date = st.date_input("End (created on/before)", value=None, key="end_date") if use_dates else None

    # Action row
    a1, a2 = st.columns([1, 5])
    with a1:
        per_page = st.selectbox("Per page", [5, 10, 20, 50], index=1, key="per_page_search")
    with a2:
        search_clicked = st.button("Search Documents", type="primary", key="search_btn")

    # Trigger conditions: explicitly click search OR any filter has content
    has_filters = any([
        search_query,
        st.session_state["tags_list_search"],
        flag_filter,
        use_dates and (start_date or end_date),
    ])
    if search_clicked or has_filters:
        try:
            # ---------- Build MongoDB query ----------
            query = {}

            # text query across fields
            if search_query:
                search_regex = {"$regex": search_query, "$options": "i"}
                query["$or"] = [
                    {"name": search_regex},
                    {"description": search_regex},
                    {"notes": search_regex},
                ]

            # tags (any of)
            tags = st.session_state["tags_list_search"]
            if tags:
                query["tags"] = {"$in": tags}

            # flags (any of)
            if flag_filter:
                query["flags"] = {"$in": flag_filter}

            # created_at date range
            if use_dates and (start_date or end_date):
                date_filter = {}
                # Convert to naive datetime at bounds
                if start_date:
                    date_filter["$gte"] = datetime.combine(start_date, time.min)
                if end_date:
                    date_filter["$lte"] = datetime.combine(end_date, time.max)
                if date_filter:
                    query["created_at"] = date_filter

            # Search
            documents = mongo_client.find(COLLECTION_NAME, query) or []

            # Sorting in Python (adjust if your driver supports sort server-side)
            if sort_by == "Newest created":
                documents.sort(key=lambda d: d.get("created_at") or d.get("updated_at") or datetime.min, reverse=True)
            elif sort_by == "Last updated":
                documents.sort(key=lambda d: d.get("updated_at") or d.get("created_at") or datetime.min, reverse=True)
            else:
                documents.sort(key=lambda d: (d.get("name") or "").lower())

            total = len(documents)
            if total == 0:
                st.info("No documents found matching your criteria.")
                return

            st.success(f"Found {total} document(s)")

            # ---------- Pagination ----------
            key_page = "search_page_idx"
            if key_page not in st.session_state:
                st.session_state[key_page] = 1
            page_count = max(1, (total + per_page - 1) // per_page)
            colp1, colp2, colp3 = st.columns([1, 2, 1])
            with colp1:
                if st.button("◀ Previous", disabled=st.session_state[key_page] <= 1):
                    st.session_state[key_page] -= 1
                    st.rerun()
            with colp2:
                st.markdown(f"<div style='text-align:center;'>Page {st.session_state[key_page]} of {page_count}</div>", unsafe_allow_html=True)
            with colp3:
                if st.button("Next ▶", disabled=st.session_state[key_page] >= page_count):
                    st.session_state[key_page] += 1
                    st.rerun()

            start = (st.session_state[key_page] - 1) * per_page
            end = min(start + per_page, total)
            page_docs = documents[start:end]

            # ---------- Results (card-style expanders) ----------
            for doc in page_docs:
                doc_name = doc.get("name", "Untitled")
                created = doc.get("created_at")
                updated = doc.get("updated_at")
                created_str = created.strftime("%Y-%m-%d %H:%M:%S") if hasattr(created, "strftime") else str(created)
                updated_str = updated.strftime("%Y-%m-%d %H:%M:%S") if hasattr(updated, "strftime") else str(updated)

                header = f"📄 {doc_name}  \nCreated: {created_str} • Updated: {updated_str}"
                with st.expander(header, expanded=False):
                    left, right = st.columns([1.2, 1])

                    with left:
                        st.markdown("**Description**")
                        st.write(doc.get("description", "—"))
                        st.markdown("**Notes**")
                        st.write(doc.get("notes", "—"))

                        # Tags as chips using multiselect
                        st.markdown("**Tags**")
                        existing_tags = doc.get("tags", []) or []
                        # Show only (not editing tags here); looks like chips
                        st.multiselect(
                            "Document tags",
                            options=existing_tags,
                            default=existing_tags,
                            key=f"tags_view_{doc['doc_id']}",
                            disabled=True,
                            help=None
                        )

                        st.markdown("**Files**")
                        files = doc.get("files", []) or []
                        if files:
                            for f in files:
                                cfa, cfb = st.columns([4, 1])
                                with cfa:
                                    st.write(f"📎 {f.get('filename','file')} ({f.get('size','?')} bytes)")
                                with cfb:
                                    if f.get("s3_url"):
                                        st.link_button("Download", f["s3_url"])
                        else:
                            st.caption("No files.")

                    with right:
                        # Flags view
                        st.markdown("**Current Flags**")
                        current_flags = doc.get("flags", []) or []
                        st.multiselect(
                            "Flags",
                            options=current_flags,
                            default=current_flags,
                            key=f"flags_view_{doc['doc_id']}",
                            disabled=True
                        )

                        st.divider()
                        st.markdown("**🏷️ Modify Flags**")
                        avail_flags = get_available_flags() or []
                        new_flags = st.multiselect(
                            "Update flags",
                            avail_flags,
                            default=current_flags,
                            key=f"flags_edit_{doc['doc_id']}"
                        )

                        # Contextual new-flag creator under the editor
                        with st.popover("➕ Create a new flag", use_container_width=True):
                            nf = st.text_input("New flag name", key=f"nf_{doc['doc_id']}")
                            if st.button("Add Flag", key=f"nf_btn_{doc['doc_id']}"):
                                if not nf:
                                    st.warning("Please enter a flag name.")
                                else:
                                    if add_new_flag(nf):
                                        st.success(f"Flag '{nf}' added.")
                                        st.toast(f"Flag '{nf}' added")
                                        st.rerun()
                                    else:
                                        st.info(f"Flag '{nf}' already exists.")

                        if st.button("Update Flags", key=f"update_{doc['doc_id']}"):
                            try:
                                update_result = mongo_client.update(
                                    COLLECTION_NAME,
                                    {"doc_id": doc["doc_id"]},
                                    {"$set": {"flags": new_flags, "updated_at": datetime.utcnow()}}
                                )
                                if update_result:
                                    st.success("Flags updated.")
                                    st.rerun()
                                else:
                                    st.error("Failed to update flags.")
                            except Exception as e:
                                st.error(f"Error updating flags: {str(e)}")

                    # Footer quick info
                    st.caption(f"Document ID: `{doc.get('doc_id','')}`")

        except Exception as e:
            st.error(f"Error searching documents: {str(e)}")

def dive_deeper_page():
    st.header("🔗 Dive Deeper")
    
    # Document selection
    try:
        documents = mongo_client.find(COLLECTION_NAME)
        if not documents:
            st.warning("No documents found. Please add some documents first.")
            return
        
        doc_options = {f"{doc['name']} (ID: {doc['doc_id'][:8]}...)": doc for doc in documents}
        selected_doc_name = st.selectbox("Select a document", list(doc_options.keys()))
        
        if selected_doc_name:
            selected_doc = doc_options[selected_doc_name]
            
            # Show document info
            st.info(f"**Selected Document:** {selected_doc['name']}\n**Description:** {selected_doc['description']}")
            
            col1, col2 = st.columns(2)
            
            with col1:
                depth = st.number_input("Crawl depth", min_value=1, max_value=5, value=2)
                
            with col2:
                max_links = st.number_input("Max links per page", min_value=5, max_value=50, value=10)
            
            if st.button("Start Deep Dive"):
                try:
                    with st.spinner("Extracting links from document and crawling..."):
                        # Find PDF files in the selected document
                        pdf_files = [f for f in selected_doc['files'] if f['filename'].lower().endswith('.pdf')]
                        
                        if not pdf_files:
                            st.error("No PDF files found in the selected document!")
                            return
                        
                        all_links = []
                        
                        # Extract links from all PDF files
                        for pdf_file in pdf_files:
                            st.info(f"Processing PDF: {pdf_file['filename']}")
                            links = extract_links_from_pdf(pdf_file['s3_url'])
                            all_links.extend(links)
                        
                        if not all_links:
                            st.warning("No links found in the PDF files!")
                            return
                        
                        st.success(f"Found {len(all_links)} links in PDF files")
                        
                        # Start crawling from extracted links
                        all_results = []
                        for link in all_links[:max_links]:  # Limit starting links
                            st.info(f"Crawling from: {link}")
                            results = crawl_links([link], depth, max_links)
                            all_results.extend(results)
                        
                        if all_results:
                            st.success(f"Crawled {len(all_results)} pages total")
                            
                            # Store crawled content in S3 and database
                            crawl_doc_id = str(uuid.uuid4())
                            crawl_document = {
                                "doc_id": crawl_doc_id,
                                "name": f"Deep Dive: {selected_doc['name']}",
                                "description": f"Deep crawl results from {selected_doc['name']} - {len(all_results)} pages crawled",
                                "tags": selected_doc['tags'] + ["deep-dive", "crawled"],
                                "notes": f"Original document: {selected_doc['doc_id']}. Crawled at depth {depth}",
                                "flags": selected_doc['flags'],
                                "original_doc_id": selected_doc['doc_id'],
                                "crawl_results": all_results,
                                "files": [],
                                "created_at": datetime.utcnow(),
                                "updated_at": datetime.utcnow()
                            }
                            
                            # Save crawled content to text files and upload to S3
                            s3_files = []
                            for i, result in enumerate(all_results):
                                content = f"URL: {result['url']}\nTitle: {result['title']}\nDepth: {result['depth']}\n\n{result['content']}"
                                
                                with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as tmp_file:
                                    tmp_file.write(content)
                                    tmp_file.flush()
                                    
                                    # Upload to S3
                                    file_key = f"{S3_FOLDER}{crawl_doc_id}/crawled_page_{i+1}_{urlparse(result['url']).netloc}.txt"
                                    success = asyncio.run(s3_client.upload_file(tmp_file.name, file_key))
                                    
                                    if success:
                                        s3_url = f"https://{s3_client.bucket_name}.s3.amazonaws.com/{file_key}"
                                        s3_files.append({
                                            "filename": f"crawled_page_{i+1}_{urlparse(result['url']).netloc}.txt",
                                            "s3_key": file_key,
                                            "s3_url": s3_url,
                                            "size": len(content),
                                            "type": "text/plain",
                                            "source_url": result['url']
                                        })
                                    
                                    os.unlink(tmp_file.name)
                            
                            crawl_document["files"] = s3_files
                            
                            # Insert crawled document into MongoDB
                            result_id = mongo_client.insert(COLLECTION_NAME, crawl_document)
                            
                            if result_id:
                                st.success(f"Crawled content saved as new document! Document ID: {crawl_doc_id}")
                            
                            # Display results
                            st.subheader("Crawl Results:")
                            for i, result in enumerate(all_results):
                                with st.expander(f"Level {result['depth']}: {result['title'][:50]}...", expanded=(i == 0)):
                                    st.write(f"**URL:** {result['url']}")
                                    st.write(f"**Title:** {result['title']}")
                                    st.write(f"**Depth:** {result['depth']}")
                                    
                                    if result['content']:
                                        st.write("**Content Preview:**")
                                        st.text_area("", result['content'][:500] + "...", height=100, key=f"content_{i}")
                                    
                                    if result['links']:
                                        st.write(f"**Found Links ({len(result['links'])}):**")
                                        for link in result['links'][:5]:  # Show first 5 links
                                            st.write(f"• {link}")
                        else:
                            st.warning("No content found during crawl")
                
                except Exception as e:
                    st.error(f"Error during deep dive: {str(e)}")
    
    except Exception as e:
        st.error(f"Error loading documents: {str(e)}")

def extract_links_from_pdf(pdf_url):
    """Extract URLs from a PDF file"""
    links = []
    try:
        # Download PDF content
        response = requests.get(pdf_url, timeout=30)
        response.raise_for_status()
        
        # Save to temporary file
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp_file:
            tmp_file.write(response.content)
            tmp_file.flush()
            
            # Method 1: Using PyMuPDF (more reliable for links)
            try:
                doc = fitz.open(tmp_file.name)
                for page_num in range(doc.page_count):
                    page = doc[page_num]
                    page_links = page.get_links()
                    
                    for link in page_links:
                        if link.get('uri'):  # External link
                            links.append(link['uri'])
                    
                    # Also extract text and look for URLs
                    text = page.get_text()
                    url_pattern = r'https?://(?:[-\w.])+(?:[:\d]+)?(?:/(?:[\w/_.])*(?:\?(?:[\w&=%.])*)?(?:#(?:\w*))?)?'
                    found_urls = re.findall(url_pattern, text)
                    links.extend(found_urls)
                
                doc.close()
                
            except Exception as e:
                st.warning(f"PyMuPDF extraction failed: {str(e)}")
                
                # Fallback: Method 2: Using PyPDF2
                try:
                    with open(tmp_file.name, 'rb') as file:
                        pdf_reader = PyPDF2.PdfReader(file)
                        
                        for page_num in range(len(pdf_reader.pages)):
                            page = pdf_reader.pages[page_num]
                            text = page.extract_text()
                            
                            # Extract URLs using regex
                            url_pattern = r'https?://(?:[-\w.])+(?:[:\d]+)?(?:/(?:[\w/_.])*(?:\?(?:[\w&=%.])*)?(?:#(?:\w*))?)?'
                            found_urls = re.findall(url_pattern, text)
                            links.extend(found_urls)
                            
                except Exception as e2:
                    st.error(f"PyPDF2 extraction also failed: {str(e2)}")
            
            # Clean up temp file
            os.unlink(tmp_file.name)
    
    except Exception as e:
        st.error(f"Error extracting links from PDF: {str(e)}")
    
    # Remove duplicates and clean links
    unique_links = list(set(links))
    valid_links = []
    
    for link in unique_links:
        if link.startswith(('http://', 'https://')):
            # Clean the link
            try:
                parsed = urlparse(link)
                clean_link = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
                if parsed.query:
                    clean_link += f"?{parsed.query}"
                valid_links.append(clean_link)
            except:
                continue
    
    return valid_links

def crawl_links(start_urls, max_depth, max_links_per_page):
    """
    Crawl links starting from a list of URLs up to specified depth
    """
    visited = set()
    results = []
    to_visit = [(url, 0) for url in start_urls]  # (url, depth)
    
    while to_visit and len(results) < 100:  # Limit total results
        current_url, current_depth = to_visit.pop(0)
        
        if current_url in visited or current_depth > max_depth:
            continue
        
        visited.add(current_url)
        
        try:
            # Get page content
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            
            response = requests.get(current_url, headers=headers, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Extract title
            title = soup.find('title')
            title_text = title.get_text().strip() if title else "No Title"
            
            # Extract text content
            # Remove script and style elements
            for script in soup(["script", "style"]):
                script.decompose()
            
            text_content = soup.get_text()
            # Clean up text
            lines = (line.strip() for line in text_content.splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            text_content = ' '.join(chunk for chunk in chunks if chunk)
            
            # Extract links
            links = []
            if current_depth < max_depth:
                for link in soup.find_all('a', href=True):
                    href = link['href']
                    full_url = urljoin(current_url, href)
                    
                    # Only follow http/https links
                    if full_url.startswith(('http://', 'https://')):
                        parsed = urlparse(full_url)
                        # Remove fragments
                        clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
                        if parsed.query:
                            clean_url += f"?{parsed.query}"
                        
                        if clean_url not in visited and len(links) < max_links_per_page:
                            links.append(clean_url)
                            to_visit.append((clean_url, current_depth + 1))
            
            # Add to results
            results.append({
                'url': current_url,
                'title': title_text,
                'content': text_content[:1000],  # Limit content length
                'links': links,
                'depth': current_depth
            })
            
        except Exception as e:
            st.warning(f"Failed to crawl {current_url}: {str(e)}")
            continue
    
    return results

if __name__ == "__main__":
    main()