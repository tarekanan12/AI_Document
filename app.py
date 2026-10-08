import os
import tempfile
import pandas as pd
import streamlit as st
from pydantic import BaseModel, Field

from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.output_parsers import StrOutputParser, PydanticOutputParser
from langchain_core.messages import HumanMessage, AIMessage


# ============================================================
# 1. Pydantic Model لاستخراج البيانات الهيكلية
# ============================================================

class DocumentMetadataSchema(BaseModel):
    document_title: str = Field(
        description="العنوان الرئيسي للمستند أو الموضوع الأساسي"
    )

    document_type: str = Field(
        description="نوع المستند مثل: تقرير مالي، عقد، ورقة بحثية، دليل أجهزة، فاتورة"
    )

    key_topics: list[str] = Field(
        description="قائمة بـ 3 إلى 5 مواضيع رئيسية يتناولها المستند"
    )

    entities_mentioned: list[str] = Field(
        description="أهم الأشخاص، الشركات، أو الجهات المذكورة في المستند"
    )

    main_summary: str = Field(
        description="ملخص تنفيذي للمستند في 2-3 جمل"
    )


# ============================================================
# 2. إعداد واجهة Streamlit
# ============================================================

st.set_page_config(
    page_title="AI Document Intelligence System",
    page_icon="📑",
    layout="wide"
)

st.title("📑 AI Document Intelligence System")
st.caption("نظام تحليلي متكامل للمستندات يعمل بنموذج Google Gemini")


# ============================================================
# Sidebar
# ============================================================

with st.sidebar:

    st.header("⚙️ الإعدادات والملفات")

    api_key = st.text_input(
        "أدخل Google Gemini API Key:",
        type="password"
    )

    uploaded_files = st.file_uploader(
        "رفع المستندات (PDF / TXT):",
        type=["pdf", "txt"],
        accept_multiple_files=True
    )

    st.divider()

    if st.button("🗑️ مسح ذاكرة المحادثة"):
        st.session_state.chat_history = []
        st.rerun()


# ============================================================
# إدارة ذاكرة المحادثة
# ============================================================

if "chat_history" not in st.session_state:
    st.session_state.chat_history = []


# ============================================================
# 3. معالجة الملفات وتقطيعها
# ============================================================

@st.cache_resource(show_spinner=False)
def process_documents(files):

    all_docs = []

    for file in files:

        suffix = f".{file.name.split('.')[-1]}"

        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=suffix
        ) as tmp_file:

            tmp_file.write(file.read())
            tmp_path = tmp_file.name

        if file.name.lower().endswith(".pdf"):

            loader = PyPDFLoader(tmp_path)
            docs = loader.load()

            for doc in docs:
                doc.metadata["source_name"] = file.name

            all_docs.extend(docs)

        elif file.name.lower().endswith(".txt"):

            loader = TextLoader(tmp_path)
            docs = loader.load()

            for doc in docs:
                doc.metadata["source_name"] = file.name

            all_docs.extend(docs)

        os.remove(tmp_path)


    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=150
    )

    splits = text_splitter.split_documents(all_docs)


    embeddings = HuggingFaceEmbeddings(
        model_name="all-MiniLM-L6-v2"
    )

    vectorstore = Chroma.from_documents(
        documents=splits,
        embedding=embeddings
    )

    return vectorstore, all_docs


# ============================================================
# 4. المنطق الرئيسي
# ============================================================

if uploaded_files and api_key:

    with st.spinner("جاري معالجة المستندات..."):

        vectorstore, full_documents = process_documents(
            uploaded_files
        )

        retriever = vectorstore.as_retriever(
            search_kwargs={"k": 4}
        )


    # ========================================================
    # Gemini Model
    # ========================================================

    llm = ChatGoogleGenerativeAI(
    model="gemini-3.8-flash",
    google_api_key=api_key
)


    # ========================================================
    # Tabs
    # ========================================================

    tab1, tab2, tab3 = st.tabs([
        "💬 المحادثة الذكية (RAG)",
        "📊 استخراج البيانات (Structured Output)",
        "📝 التلخيص التنفيذي"
    ])


    # ========================================================
    # TAB 1 - RAG
    # ========================================================

    with tab1:

        col_chat, col_sources = st.columns([2, 1])


        with col_chat:

            st.subheader("محادثة تفاعلية مع المستندات")


            for msg in st.session_state.chat_history:

                role = (
                    "user"
                    if isinstance(msg, HumanMessage)
                    else "assistant"
                )

                with st.chat_message(role):
                    st.write(msg.content)


            if user_input := st.chat_input(
                "اسأل أي سؤال حول المستندات المرفوعة..."
            ):

                st.session_state.chat_history.append(
                    HumanMessage(content=user_input)
                )


                with st.chat_message("user"):
                    st.write(user_input)


                retrieved_docs = retriever.invoke(
                    user_input
                )


                context_text = "\n\n".join(
                    [
                        f"[المصدر: "
                        f"{doc.metadata.get('source_name', 'مستند')} "
                        f"| صفحة: "
                        f"{doc.metadata.get('page', 0) + 1}]\n"
                        f"{doc.page_content}"
                        for doc in retrieved_docs
                    ]
                )


                rag_prompt = ChatPromptTemplate.from_messages([

                    (
                        "system",

                        "أنت مساعد ذكي متخصص في تحليل البيانات. "
                        "أجب بناءً على السياق المتاح فقط. "
                        "إذا لم تجد الإجابة، قل "
                        "'المعلومة غير متوفرة في المستندات'.\n\n"
                        "السياق:\n{context}"
                    ),

                    MessagesPlaceholder("chat_history"),

                    ("human", "{input}")

                ])


                rag_chain = (
                    rag_prompt
                    | llm
                    | StrOutputParser()
                )


                with st.chat_message("assistant"):

                    with st.spinner(
                        "جاري البحث والاستخراج..."
                    ):

                        response = rag_chain.invoke({

                            "context": context_text,

                            "input": user_input,

                            "chat_history":
                                st.session_state.chat_history[:-1]

                        })

                        st.write(response)


                st.session_state.chat_history.append(
                    AIMessage(content=response)
                )

                st.session_state["latest_sources"] = (
                    retrieved_docs
                )


        with col_sources:

            st.subheader("📌 المصادر المستخدمة")


            if "latest_sources" in st.session_state:

                for idx, doc in enumerate(
                    st.session_state["latest_sources"]
                ):

                    src = doc.metadata.get(
                        "source_name",
                        "مستند"
                    )

                    page = doc.metadata.get(
                        "page",
                        None
                    )

                    page_str = (
                        f" - صفحة {page + 1}"
                        if page is not None
                        else ""
                    )


                    with st.expander(
                        f"مصدر {idx + 1}: "
                        f"{src}{page_str}"
                    ):

                        st.caption(
                            doc.page_content
                        )

            else:

                st.info(
                    "ستظهر المصادر المعتمد عليها هنا "
                    "بعد طرح السؤال."
                )


    # ========================================================
    # TAB 2 - STRUCTURED JSON
    # ========================================================

    with tab2:

        st.subheader(
            "استخراج المكونات الرئيسية والهيكلية "
            "(Structured JSON Output)"
        )


        if st.button(
            "🚀 استخراج البيانات الهيكلية الآن"
        ):

            with st.spinner(
                "جاري تحليل الهيكل وتفريغ البيانات..."
            ):

                pydantic_parser = (
                    PydanticOutputParser(
                        pydantic_object=
                        DocumentMetadataSchema
                    )
                )


                combined_sample = "\n\n".join(
                    [
                        d.page_content
                        for d in full_documents[:5]
                    ]
                )


                extract_prompt = (
                    ChatPromptTemplate.from_template(

                        "أنت خبير تحليل مستندات. "
                        "استخرج المكونات الهيكلية التالية "
                        "من النص المرفق بالتفصيل.\n"

                        "{format_instructions}\n\n"

                        "النص المراد تحليله:\n"
                        "{text}"
                    )
                )


                extraction_chain = (
                    extract_prompt
                    | llm
                    | pydantic_parser
                )


                try:

                    parsed_result = (
                        extraction_chain.invoke({

                            "text": combined_sample,

                            "format_instructions":
                                pydantic_parser
                                .get_format_instructions()

                        })
                    )


                    st.success(
                        "تم استخراج البيانات بنجاح!"
                    )


                    st.json(
                        parsed_result.model_dump()
                    )


                    df_data = {

                        "خاصية البيانات": [

                            "عنوان المستند",

                            "نوع المستند",

                            "أهم الكيانات المذكورة",

                            "الملخص التنفيذي"

                        ],

                        "القيمة المستخرجة": [

                            parsed_result.document_title,

                            parsed_result.document_type,

                            ", ".join(
                                parsed_result.entities_mentioned
                            ),

                            parsed_result.main_summary

                        ]

                    }


                    st.table(
                        pd.DataFrame(df_data)
                    )


                except Exception as e:

                    st.error(
                        f"حدث خطأ أثناء الاستخراج: {e}"
                    )


    # ========================================================
    # TAB 3 - SUMMARIZATION
    # ========================================================

    with tab3:

        st.subheader(
            "توليد الملخص الشامل للمستندات"
        )


        if st.button(
            "📝 توليد ملخص تنفيذي"
        ):

            with st.spinner(
                "جاري صياغة الملخص..."
            ):

                full_text = "\n\n".join(
                    [
                        d.page_content
                        for d in full_documents[:10]
                    ]
                )


                summary_prompt = (
                    ChatPromptTemplate.from_template(

                        "أنت محلل بيانات تنفيذي. "
                        "قدم ملخصاً شاملاً للنص المرفق "
                        "مقسماً إلى:\n"

                        "1. النقاط الرئيسية (Key Takeaways)\n"

                        "2. القرارات أو التوصيات المطلوبة\n"

                        "3. نبرة المستند والسياق العام.\n\n"

                        "النص:\n{text}"
                    )
                )


                summary_chain = (
                    summary_prompt
                    | llm
                    | StrOutputParser()
                )


                summary_output = (
                    summary_chain.invoke({
                        "text": full_text
                    })
                )


                st.markdown(
                    summary_output
                )


# ============================================================
# رسائل البداية
# ============================================================

elif not api_key:

    st.info(
        "👈 أدخل Google Gemini API Key "
        "في القائمة الجانبية للبدء."
    )

else:

    st.info(
        "👈 قم برفع مستند واحد أو أكثر "
        "في القائمة الجانبية للبدء."
    )