(function () {
  const BASE = '';
  const el = (id) => document.getElementById(id);

  function qs(selector, root) {
    return (root || document).querySelector(selector);
  }
  function qsa(selector, root) {
    return Array.from((root || document).querySelectorAll(selector));
  }

  function setToast(message) {
    let toast = el("toast");
    if (!toast) {
      toast = document.createElement("div");
      toast.id = "toast";
      toast.className = "toast";
      document.body.appendChild(toast);
    }
    toast.hidden = false;
    toast.textContent = message;
    window.clearTimeout(setToast._t);
    setToast._t = window.setTimeout(() => {
      toast.hidden = true;
      toast.textContent = "";
    }, 2600);
  }

  async function api(path, options = {}) {
    options.credentials = options.credentials || "same-origin";

    let maxRetries = 5;
    let delayMs = 3000;
    let attempt = 0;

    while (attempt <= maxRetries) {
      try {
        const res = await fetch(path, options);

        if (!res.ok && [429, 503, 504].includes(res.status) && attempt < maxRetries) {
            attempt++;
            setToast(`High traffic! Waiting for an open AI slot... Retrying in ${delayMs/1000}s.`);
            await new Promise(r => setTimeout(r, delayMs));
            delayMs *= 2;
            continue;
        }

        const rawText = await res.text();
        let data = {};

        try {
            data = JSON.parse(rawText);
        } catch(e) {
            console.error(`====== API ERROR FOR: ${path} ======`);
            console.error(`Status: ${res.status} ${res.statusText}`);
            console.error(`Raw HTML Response:\n${rawText.substring(0, 1500)}`);
            throw new Error(`Server returned an unexpected ${res.status} HTML response instead of JSON. Check the F12 browser console for the full error log!`);
        }

        if (!res.ok) {
          let err = data && data.error ? data.error : `Request failed: ${res.status}`;
          if (attempt >= maxRetries && [429, 503, 504].includes(res.status)) {
              err = "The AI servers are currently too busy. Please wait a few minutes and try again!";
          }
          throw new Error(err);
        }

        return data;
      } catch (error) {
          if (error.name === 'TypeError' && attempt < maxRetries) {
              attempt++;
              setToast(`Network connection lost! Retrying in ${delayMs/1000}s...`);
              await new Promise(r => setTimeout(r, delayMs));
              delayMs *= 2;
              continue;
          }
          throw error;
      }
    }
  }

  // Function to refresh UI credits without reloading the page
  window.refreshUserCredits = async function() {
      try {
          const data = await api(BASE + '/api/user/me');
          if (data.ok) {
              const els = document.querySelectorAll('#credits, #userCredits, #navCredits, .credits, .user-credits, [data-credits]');
              els.forEach(el => {
                  let updated = false;
                  el.childNodes.forEach(node => {
                      if (node.nodeType === Node.TEXT_NODE && node.nodeValue.toLowerCase().includes('credits:')) {
                          node.nodeValue = node.nodeValue.replace(/credits:\s*[\d.]+/i, `Credits: ${data.credits}`);
                          updated = true;
                      }
                  });
                  if (!updated) {
                      if (el.innerText.toLowerCase().includes('credits:')) el.innerText = `Credits: ${data.credits}`;
                      else el.innerText = data.credits;
                  }
              });

              // Aggressive fallback to catch any missing ID tags showing credits
              document.querySelectorAll('span, div, b, strong, p, a, button').forEach(el => {
                  if (el.childNodes.length === 1 && el.innerText && el.innerText.trim().toLowerCase().startsWith('credits:')) {
                      el.innerText = `Credits: ${data.credits}`;
                  }
              });
          }
      } catch (e) {}
  };

  async function fetchStreaming(path, options = {}, onProgress) {
    options.credentials = options.credentials || "same-origin";

    let maxRetries = 5;
    let delayMs = 3000;
    let attempt = 0;

    while (attempt <= maxRetries) {
      try {
        const res = await fetch(path, options);

        if (!res.ok) {
          if ([429, 503, 504].includes(res.status) && attempt < maxRetries) {
              attempt++;
              onProgress({status: "progress", message: `High traffic! Waiting for an open AI slot... Retrying in ${delayMs/1000}s.`});
              await new Promise(r => setTimeout(r, delayMs));
              delayMs *= 2;
              continue;
          }

          const rawText = await res.text();
          let err = `Request failed: ${res.status}`;
          try {
              const data = JSON.parse(rawText);
              if (data.error) err = data.error;
          } catch(e){
              console.error(`[Streaming Error] Status: ${res.status}. Raw Response:\n`, rawText.substring(0, 1000));
              err = `Server returned ${res.status} HTML error. Check F12 browser console.`;
          }

          // Handle credit error specifically
          if (res.status === 402) {
              onProgress({status: "error", message: "Insufficient credits. Please contact an admin to top up."});
              throw new Error("Insufficient credits.");
          }

          if (attempt >= maxRetries && [429, 503, 504].includes(res.status)) {
              err = "The AI servers are currently too busy. Please wait a few minutes and try again!";
          }
          throw new Error(err);
        }

        const reader = res.body.getReader();
        const decoder = new TextDecoder("utf-8");
        let buffer = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop(); // keep last incomplete line
          for (const line of lines) {
            if (line.trim()) {
              onProgress(JSON.parse(line));
            }
          }
        }
        if (buffer.trim()) {
          onProgress(JSON.parse(buffer));
        }
        return; // Success, exit retry loop

      } catch (error) {
          if (error.name === 'TypeError' && attempt < maxRetries) {
              attempt++;
              onProgress({status: "progress", message: `Network connection lost! Retrying in ${delayMs/1000}s...`});
              await new Promise(r => setTimeout(r, delayMs));
              delayMs *= 2;
              continue;
          }
          throw error;
      }
    }
  }

  // Register PWA Service Worker
  if ('serviceWorker' in navigator) {
      navigator.serviceWorker.register(BASE + '/sw.js').catch(err => console.log('SW setup failed', err));
  }

  // ------------------------
  // Dashboard actions
  // ------------------------
  document.addEventListener("click", async (e) => {
    if (e.target.closest(".btn-delete-syllabus")) {
      const btn = e.target.closest(".btn-delete-syllabus");
      const id = btn.dataset.id;
      if (!id || !confirm("Are you sure you want to delete this syllabus?")) return;

      try {
        btn.disabled = true;
        await api(BASE + `/api/syllabus/${encodeURIComponent(id)}`, { method: "DELETE" });
        setToast("Syllabus deleted");
        window.location.reload();
      } catch (err) {
        setToast(err.message || String(err));
        btn.disabled = false;
      }
    }
  });

  // ------------------------
  // Tabs (syllabus page)
  // ------------------------
  function wireTabs() {
    const tabsWrap = qs(".tabs-wrap");
    if (!tabsWrap) return;

    const tabs = qsa(".tab", tabsWrap);
    const panels = qsa(".tab-panel");

    function setActive(panelKey) {
      tabs.forEach((t) => {
        const isActive = t.dataset.tab === panelKey;
        t.classList.toggle("active", isActive);
        t.setAttribute("aria-selected", isActive ? "true" : "false");
      });

      panels.forEach((p) => {
        const isActive = p.dataset.panel === panelKey;
        p.classList.toggle("active", isActive);
      });
    }

    tabs.forEach((t) => {
      t.addEventListener("click", () => setActive(t.dataset.tab));
    });
  }

  // ------------------------
  // Upload page
  // ------------------------
  function wireUpload() {
    // Dynamically inject text area to global forms handling PDF upload
    const globalForms = document.querySelectorAll("form");
    globalForms.forEach(form => {
        const fileInput = qs("input[type='file']", form) || qs("input[name='syllabus_pdf']", form);
        if (fileInput && !qs("textarea[name='syllabus_text']", form)) {
            const taDiv = document.createElement("div");
            taDiv.style.marginTop = "15px";
            taDiv.innerHTML = `<label style="display:block; margin-bottom:5px; font-weight:bold;">Or Paste Syllabus Text (Optional)</label><textarea name="syllabus_text" rows="4" placeholder="If your Document is unreadable, paste the text here..." style="width: 100%; padding: 10px; background: rgba(0,0,0,0.2); border-radius: 6px; border: 1px solid rgba(255,255,255,0.1); color: inherit; resize: vertical;"></textarea>`;
            fileInput.parentNode.insertBefore(taDiv, fileInput.nextSibling);
        }
    });

    // 1. Globally delegate file input change to handle dynamically loaded forms
    document.addEventListener("change", (e) => {
      const target = e.target;
      if (target && target.tagName === "INPUT" && target.type === "file") {
        target.name = "syllabus_pdf";
        const form = target.closest("form");
        if (!form) return;

        let fileHint = qs(".file-name", form) || el("fileName");
        if (!fileHint) {
          fileHint = document.createElement("div");
          fileHint.className = "file-name";
          fileHint.style.marginTop = "8px";
          fileHint.style.fontWeight = "bold";
          target.parentNode.insertBefore(fileHint, target.nextSibling);
        }
        const name = target.files && target.files[0] ? target.files[0].name : "No file selected";
        fileHint.textContent = "Selected: " + name;
        fileHint.style.color = name !== "No file selected" ? "#4ade80" : "inherit";
      }
    });

    // 2. Globally intercept form submissions to prevent default GET behavior
    document.addEventListener("submit", async (e) => {
      const form = e.target;
      if (!form) return;

      const fileInput = qs("input[type='file']", form) || qs("input[name='syllabus_pdf']", form);

      // Identify if this is the upload form
      if (fileInput || form.id === "uploadForm") {
        e.preventDefault(); // STOP THE BROWSER FROM NATIVE REDIRECTING

        const errorBox = el("uploadError") || qs(".upload-error", form);
        const spinner = qs(".spinner", form);
        const submitBtn = qs("button[type='submit']", form) || qs("button", form);
        const textInput = qs("textarea[name='syllabus_text']", form);

        if ((!fileInput || !fileInput.files || !fileInput.files[0]) && (!textInput || !textInput.value.trim())) {
          const msg = "Please select a PDF/Word file or paste text first.";
          if (errorBox) errorBox.textContent = msg;
          setToast(msg);
          return;
        }

        if (fileInput && fileInput.files && fileInput.files[0]) {
          if (fileInput.files[0].size > 10 * 1024 * 1024) {
            const msg = "File size exceeds 10MB limit.";
            if (errorBox) errorBox.textContent = msg;
            setToast(msg);
            return;
          }
        }

        if (errorBox) errorBox.textContent = "";

        try {
          if (submitBtn) {
            submitBtn.disabled = true;
            submitBtn.dataset.origText = submitBtn.innerHTML;
            submitBtn.innerHTML = "Uploading & AI Analyzing... please wait <span class='spinner'></span>";
          }
          if (spinner) spinner.hidden = false;

          const fd = new FormData(form);
          if (!fd.get("syllabus_pdf") && fileInput && fileInput.files && fileInput.files[0]) {
            fd.append("syllabus_pdf", fileInput.files[0]);
          }
          if (!fd.get("course_name") || !fd.get("course_name").toString().trim()) {
            fd.set("course_name", "General Course");
          }
          if (!fd.get("class_name") || !fd.get("class_name").toString().trim()) {
            fd.set("class_name", "General Class");
          }

          const uploadActionUrl = form.getAttribute("action") || (BASE + "/api/upload");
          const resp = await fetch(uploadActionUrl, { method: "POST", body: fd });
          const data = await resp.json().catch(() => ({}));

          if (!resp.ok || !data.ok) {
            throw new Error((data && data.error) ? data.error : "Upload failed");
          }

          setToast("Upload successful! Redirecting to syllabus...");
          window.location.href = BASE + `/syllabus/${data.syllabus_id}`;
        } catch (err) {
          if (errorBox) errorBox.textContent = err.message || String(err);
          setToast("Error: " + (err.message || String(err)));
        } finally {
          if (submitBtn && submitBtn.dataset.origText) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = submitBtn.dataset.origText;
          }
          if (spinner) spinner.hidden = true;
        }
      }
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
      // Auto-wrap logout link in a styled dropdown menu
      const logoutLink = qs('a[href$="/logout"]');
      if (logoutLink && logoutLink.parentElement) {
          const wrapper = document.createElement("div");
          wrapper.className = "nav-user-dropdown";
          wrapper.innerHTML = `<div class="nav-account-btn">Account ▾</div><div class="dropdown-content"></div>`;
          logoutLink.parentElement.insertBefore(wrapper, logoutLink);
          wrapper.querySelector(".dropdown-content").appendChild(logoutLink);
      }

      // Add Admin link if user is admin
      api(BASE + '/api/user/me').then(data => {
          if (data.ok && data.role === 'admin') {
              qs('.dropdown-content')?.insertAdjacentHTML('afterbegin', `<a href="${BASE}/admin">Admin Panel</a>`);
          }
      }).catch(() => {});

      // Auto-inject hamburger menu logic for mobile
      const nav = document.querySelector("nav") || document.querySelector(".navbar");
      if (nav && !document.querySelector(".nav-hamburger")) {
          const elements = Array.from(nav.children);
          const linkContainer = document.createElement("div");
          linkContainer.className = "nav-links";

          // Move everything except the logo and theme button into the link container
          elements.forEach(el => {
              if (!el.classList.contains("logo") && !el.querySelector("img") && el.tagName !== "H1" && el.id !== "btnToggleTheme") {
                  linkContainer.appendChild(el);
              }
          });

          const btn = document.createElement("button");
          btn.className = "nav-hamburger";
          btn.innerHTML = "☰";
          btn.addEventListener("click", () => linkContainer.classList.toggle("show"));

          nav.appendChild(btn);
          nav.appendChild(linkContainer);
      }

  });

  // ✅ Renders Markdown formatting if marked.js is available
  function renderMarkdown(s, inline = false) {
    if (s === null || s === undefined) return "";
    const str = String(s);
    if (typeof marked !== 'undefined') {
      return inline ? marked.parseInline(str) : marked.parse(str);
    }
    return escapeHtml(str);
  }

  // ✅ Helper to re-trigger smooth entrance animations dynamically
  function triggerAnimation(el) {
    if (!el) return;
    el.classList.remove("animate-in");
    void el.offsetWidth; // Force DOM reflow
    el.classList.add("animate-in");
  }

  // Setup TTS
  let currentUtterance = null;
  function setupTTS() {
      el("btnTTSPlay")?.addEventListener("click", () => {
          window.speechSynthesis.cancel();
          const mainContent = el("notesMain")?.innerText || "";
          if (!mainContent) return setToast("No text to read");

          const sentences = mainContent.split(/(?<=[.?!])\s+/);
          let i = 0;
          function speakNext() {
              if (i >= sentences.length) return;
              currentUtterance = new SpeechSynthesisUtterance(sentences[i]);
              currentUtterance.rate = parseFloat(el("ttsSpeed")?.value || 1);
              currentUtterance.onend = () => { i++; speakNext(); };
              window.speechSynthesis.speak(currentUtterance);
          }
          speakNext();
          setToast("Reading started...");
      });
      el("btnTTSStop")?.addEventListener("click", () => {
          window.speechSynthesis.cancel();
          setToast("Reading stopped.");
      });
  }

  // Setup Theme Toggle
  document.addEventListener("click", (e) => {
    if (e.target && e.target.closest("#btnToggleTheme")) {
        document.body.classList.toggle("dark-mode");
        localStorage.setItem("theme", document.body.classList.contains("dark-mode") ? "dark" : "light");
    }
  });
  if (localStorage.getItem("theme") === "dark") document.body.classList.add("dark-mode");

  // ------------------------
  // Syllabus page rendering
  // ------------------------
  function renderNotes(notesJson, syllabusId, refreshCallback) {
    const unitsEl = el("notesUnits");
    const chatEl = el("notesChat");
    const mainEl = el("notesMain");
    if (!unitsEl || !mainEl || !chatEl) return;

    unitsEl.innerHTML = "";
    mainEl.innerHTML = "";

    const units = notesJson && notesJson.units ? notesJson.units : [];

    if (!units.length) {
      mainEl.innerHTML = `
        <div class="empty big">
          <div class="empty-icon"></div>
          <div class="empty-title">No notes yet</div>
          <div class="empty-sub">Click “Generate Notes”.</div>
        </div>
      `;
      return;
    }

    function activateUnit(i) {
      qsa(".side-item", unitsEl).forEach((node, idx) => {
        node.classList.toggle("active", idx === i);
      });

      const u = units[i];
      const topics = u && u.topics ? u.topics : [];

      let combinedNotes = `# ${u.unit_name}\n${u.notes || ""}\n\n`;
      topics.forEach(t => { combinedNotes += `## ${t.topic_name}\n${t.topic_notes}\n\n`; });

      chatEl.innerHTML = `
        <div class="panel-head" style="padding: 15px; border-bottom: 1px solid var(--border-color); background: transparent;">
            <h3 style="margin:0; font-size:16px;">Sage AI Chat - ${escapeHtml(u.unit_name)}</h3>
        </div>
        <div class="chat-history" id="chatHistory" style="flex: 1; overflow-y: auto; padding: 15px; display: flex; flex-direction: column; gap: 10px;">
        </div>
        <div class="chat-input-area" style="padding: 15px; border-top: 1px solid var(--border-color); display: flex; gap: 10px;">
            <input type="text" id="chatInput" class="neumorphic-input" placeholder="Ask Sage AI..." />
            <button class="btn primary" id="btnSendChat">Send</button>
        </div>
      `;

      mainEl.innerHTML = `
        <div class="unit-title">${escapeHtml(u.unit_name || "Unit")}</div>
        <div class="mega" style="margin-bottom:12px">${renderMarkdown(u.notes || "")}</div>
        <div class="hr"></div>
        <div style="display:grid; gap:10px">
          ${topics.map((t, tIdx) => `
            <div class="qa">
              <div style="display:flex; justify-content:space-between;">
                  <div class="q">${renderMarkdown(t.topic_name || "")}</div>
                  <button class="btn ghost sm btn-edit-note" data-unit="${i}" data-topic="${tIdx}" style="font-size:12px; padding:2px 8px;">Edit</button>
              </div>
              <div class="a" id="note-a-${i}-${tIdx}">${renderMarkdown(t.topic_notes || "")}</div>
            </div>
          `).join("")}
        </div>
      `;

      qsa(".btn-edit-note", mainEl).forEach(btn => {
          btn.addEventListener("click", () => {
              const uIdx = btn.dataset.unit;
              const tIdx = btn.dataset.topic;
              const contentBox = el(`note-a-${uIdx}-${tIdx}`);
              const rawText = notesJson.units[uIdx].topics[tIdx].topic_notes;

              contentBox.innerHTML = `
                  <textarea style="height:150px; margin-bottom:10px;">${rawText}</textarea>
                  <button class="btn primary sm btn-save-note">Save</button> <button class="btn ghost sm btn-cancel-note">Cancel</button>
              `;

              qs(".btn-cancel-note", contentBox).onclick = () => activateUnit(i);
              qs(".btn-save-note", contentBox).onclick = async () => {
                  const newVal = qs("textarea", contentBox).value;
                  notesJson.units[uIdx].topics[tIdx].topic_notes = newVal;
                  await api(BASE + `/api/syllabus/${encodeURIComponent(syllabusId)}/update_json`, { method: "POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({field: "notes_json", data: notesJson}) });
                  setToast("Saved!");
                  if(refreshCallback) refreshCallback();
              };
          });
      });

      triggerAnimation(mainEl);
      triggerAnimation(chatEl);

      const chatHistoryEl = el("chatHistory");

      if(syllabusId) {
          api(BASE + `/api/syllabus/${encodeURIComponent(syllabusId)}/chat/${encodeURIComponent(u.unit_name)}`).then(data => {
              if(data.history) {
                  data.history.forEach(msg => {
                      addChatMessage(msg.role, msg.message);
                  });
              }
          });
      }

      function addChatMessage(role, text) {
          const div = document.createElement("div");
          div.className = `chat-msg animate-in ${role === 'user' ? 'user-msg' : 'ai-msg'}`;
          div.innerHTML = renderMarkdown(text);
          chatHistoryEl.appendChild(div);
          chatHistoryEl.scrollTop = chatHistoryEl.scrollHeight;
      }

      el("btnSendChat").addEventListener("click", () => {
          const input = el("chatInput");
          const query = input.value.trim();
          if(!query) return;

          input.value = "";
          addChatMessage("user", query);

          const typingDiv = document.createElement("div");
          typingDiv.textContent = "Sage AI is thinking...";
          typingDiv.style.alignSelf = "flex-start";
          typingDiv.style.color = "rgba(255,255,255,0.5)";
          chatHistoryEl.appendChild(typingDiv);
          chatHistoryEl.scrollTop = chatHistoryEl.scrollHeight;

          fetchStreaming(BASE + `/api/syllabus/${encodeURIComponent(syllabusId)}/chat`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ unit_name: u.unit_name, query: query, notes_context: combinedNotes })
          }, (data) => {
              if (data.status === "error") {
                  typingDiv.textContent = "Error: " + data.message;
              } else if (data.status === "done") {
                  chatHistoryEl.removeChild(typingDiv);
                  addChatMessage("model", data.response);
              }
          }).catch(err => {
              typingDiv.textContent = "Error: " + err.message;
          });
      });

      el("chatInput").addEventListener("keypress", (e) => {
          if(e.key === "Enter") el("btnSendChat").click();
      });
    }

    units.forEach((u, i) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "side-item" + (i === 0 ? " active" : "");
      btn.textContent = u.unit_name || `Unit ${i + 1}`;
      btn.addEventListener("click", () => activateUnit(i));
      unitsEl.appendChild(btn);
    });

    activateUnit(0);
  }

  function renderPractice(practiceJson, attemptSummary) {
    const unitsEl = el("practiceUnits");
    const mainEl = el("practiceMain");
    if (!unitsEl || !mainEl) return;

    unitsEl.innerHTML = "";
    const topicsEl = el("practiceTopics");
    if (topicsEl && topicsEl.parentElement) topicsEl.parentElement.remove();

    const units = practiceJson && practiceJson.units ? practiceJson.units : [];

    if (!units.length) {
      if (el("quizWrap")) el("quizWrap").hidden = true;
      let eb = mainEl.querySelector(".empty.big");
      if (!eb) {
        eb = document.createElement("div");
        eb.className = "empty big";
        mainEl.appendChild(eb);
      }
      eb.innerHTML = `
        <div class="empty-icon"></div>
        <div class="empty-title">No practice yet</div>
        <div class="empty-sub">Click “Generate Practice”.</div>
      `;
      return;
    }

    const summaryByKey = new Map();
    (attemptSummary || []).forEach((s) => {
      if (!summaryByKey.has(s.unit_index)) {
        summaryByKey.set(s.unit_index, { attempts: 0, best_score: 0 });
      }
      let current = summaryByKey.get(s.unit_index);
      current.attempts += s.attempts;
      if (s.best_score > current.best_score) current.best_score = s.best_score;
    });

    let activeUnitIndex = 0;

    const quizWrap = el("quizWrap");
    const quizBody = el("quizBody");
    const quizMeta = el("quizMeta");
    const quizResult = el("quizResult");
    const quizTimer = el("quizTimer");
    const btnRestart = el("btnRestartQuiz");
    const btnSubmit = el("btnSubmitQuiz");

    let timerInterval = null;
    let startTs = 0;
    let quizQuestions = [];
    let selections = [];
    let state = { unitIndex: 0, topicIndex: 0 };

    function formatTime(sec) {
      const m = Math.floor(sec / 60);
      const s = sec % 60;
      return String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
    }

    function stopTimer() {
      if (timerInterval) window.clearInterval(timerInterval);
      timerInterval = null;
    }

    function updateTimer() {
      const now = Date.now();
      const elapsedSec = Math.max(0, Math.floor((now - startTs) / 1000));
      if (quizTimer) quizTimer.textContent = formatTime(elapsedSec);
      return elapsedSec;
    }

    function renderQuizStartScreen(unitIndex) {
      state = { unitIndex };
      const unit = units[unitIndex];
      const s = summaryByKey.get(unitIndex) || { attempts: 0, best_score: 0 };

      quizWrap.hidden = true;
      mainEl.querySelector(".empty.big")?.remove();
      el("quizStartScreen")?.remove();

      const startDiv = document.createElement("div");
      startDiv.className = "empty big animate-in";
      startDiv.id = "quizStartScreen";
      startDiv.innerHTML = `
          <div class="empty-icon"></div>
          <div class="empty-title">${escapeHtml(unit.unit_name || "Unit")}</div>
          <div class="empty-sub">Attempts: ${s.attempts} | Best Score: ${s.best_score}</div>
          <button class="btn primary" id="btnActuallyStartQuiz" style="margin-top: 20px; font-size: 1.1em; padding: 10px 24px;">Start Quiz</button>
      `;
      mainEl.appendChild(startDiv);
      el("btnActuallyStartQuiz").addEventListener("click", () => { startDiv.remove(); startQuiz(unitIndex); });
    }

    function startQuiz(unitIndex) {
      selections = [];
      quizQuestions = [];
      quizBody.innerHTML = "";
      quizResult.textContent = "";
      quizMeta.textContent = "";

      const unit = units[unitIndex];
      const questions = unit.questions || [];

      quizQuestions = questions;
      quizTimer.textContent = "00:00";
      quizWrap.hidden = false;
      mainEl.querySelector(".empty.big")?.remove();
      triggerAnimation(quizWrap);

      quizMeta.textContent = `${unit.unit_name || "Unit"}`;

      function renderQuestion(index) {
          if (questions.length === 0) {
              quizBody.innerHTML = `<div class="empty-sub">No questions found for this unit.</div>`;
              return;
          }
          const q = questions[index];
          if (!q) {
              quizBody.innerHTML = `<div class="empty-sub" style="color: #f87171;">Error: Question data is corrupted.</div>`;
              return;
          }
          const safeOpts = Array.isArray(q.options) ? q.options : (typeof q.options === 'object' && q.options !== null ? Object.values(q.options) : [String(q.options || "")]);
          quizBody.style.opacity = 0;

          setTimeout(() => {
              quizBody.innerHTML = `
                <div class="q-card" style="border: none; background: transparent; padding: 0;">
                      <div class="pill soft" style="display:inline-block; margin-bottom:12px; font-size:12px; letter-spacing:0.5px;">Topic: ${escapeHtml(q.topic_name || "General")}</div>
                  <div class="q-title" style="font-size: 1.1em; margin-bottom: 15px;">Q${index + 1} of ${questions.length}. ${renderMarkdown(String(q.question || "").replace(/<([a-zA-Z\/])/g, '&lt;$1'))}</div>
                  <div class="options" style="display:flex; flex-direction:column; gap:10px;">
                    ${safeOpts.slice(0, 4).map((opt, oi) => {
                        const isSelected = selections[index] === oi;
                        return `
                        <button type="button" class="opt ${isSelected ? 'selected' : ''}" data-opt-index="${oi}">
                          ${renderMarkdown(String(opt || "").replace(/<([a-zA-Z\/])/g, '&lt;$1')).replace(/^<p>|<\/p>\n?$/g, '')}
                        </button>
                      `;}).join("")}
                  </div>
                </div>
                <div style="display: flex; justify-content: space-between; margin-top: 25px;">
                    <button class="btn ghost" id="btnPrevQuiz" ${index === 0 ? 'disabled' : ''}>Previous</button>
                    <button class="btn primary" id="btnNextQuiz" style="display: ${index === questions.length - 1 ? 'none' : 'block'}">Next</button>
                </div>
              `;

              quizBody.style.transition = "opacity 0.3s ease";
              quizBody.style.opacity = 1;

              qsa(".opt", quizBody).forEach((node) => {
                  node.addEventListener("click", () => {
                      const oi = parseInt(node.dataset.optIndex, 10);
                      selections[index] = oi;
                      qsa(".opt", quizBody).forEach((b) => b.classList.remove("selected"));
                      node.classList.add("selected");
                  });
              });

              const prevBtn = el("btnPrevQuiz");
              if (prevBtn) prevBtn.addEventListener("click", () => renderQuestion(index - 1));

              const nextBtn = el("btnNextQuiz");
              if (nextBtn) nextBtn.addEventListener("click", () => renderQuestion(index + 1));

          }, 300);
      }

      renderQuestion(0);
      if(btnSubmit) btnSubmit.style.display = 'inline-block';

      stopTimer();
      startTs = Date.now();
      timerInterval = window.setInterval(() => {
        updateTimer();
      }, 250);
      updateTimer();
    }

    function renderUnitList() {
      unitsEl.innerHTML = "";
      units.forEach((u, ui) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "side-item" + (ui === activeUnitIndex ? " active" : "");

        const s = summaryByKey.get(ui);
        const badge = s && s.attempts ? ` • Best: ${s.best_score}` : "";
        btn.innerHTML = `<span>${escapeHtml(u.unit_name || `Unit ${ui + 1}`)}</span><span class="muted" style="float:right; font-size:12px;">${badge}</span>`;

        btn.addEventListener("click", () => {
          activeUnitIndex = ui;
          qsa(".side-item", unitsEl).forEach((n, idx) => n.classList.toggle("active", idx === ui));
          renderQuizStartScreen(activeUnitIndex);
        });
        unitsEl.appendChild(btn);
      });
    }

    // initial render
    if (quizWrap) quizWrap.hidden = true;
    renderUnitList();
    if (units.length > 0) {
      renderQuizStartScreen(0);
    } else {
      let emptyBig = mainEl.querySelector(".empty.big");
      if (!emptyBig) {
        emptyBig = document.createElement("div");
        emptyBig.className = "empty big";
        mainEl.appendChild(emptyBig);
      }
      emptyBig.innerHTML = `
        <div class="empty-icon"></div>
        <div class="empty-title">Select a unit</div>
        <div class="empty-sub">Then start the MCQ quiz for that unit.</div>
      `;
    }

    if (btnSubmit) {
      btnSubmit.onclick = async () => {
        if (!quizQuestions.length) return;

        stopTimer();
        const elapsedSec = updateTimer();

        let correct = 0;
        quizQuestions.forEach((q, qi) => {
          const ans =
            typeof q.answer_index === "number"
              ? q.answer_index
              : parseInt(q.answer_index, 10);
          const sel = selections[qi];
          if (typeof sel === "number" && sel === ans) correct += 1;
        });

        const total = quizQuestions.length;
        const score = correct;
        quizResult.textContent = `Result: ${correct}/${total} correct.`;

        quizBody.innerHTML = "";
        const resultsDiv = document.createElement("div");
        resultsDiv.style.display = "flex";
        resultsDiv.style.flexDirection = "column";
        resultsDiv.style.gap = "20px";

        quizQuestions.forEach((q, qi) => {
            const ans = typeof q.answer_index === "number" ? q.answer_index : parseInt(q.answer_index, 10);
            const sel = selections[qi];
            const isCorrect = sel === ans;

            const qDiv = document.createElement("div");
            qDiv.className = "q-card animate-in";
            qDiv.style.border = isCorrect ? "1px solid rgba(74, 222, 128, 0.4)" : "1px solid rgba(248, 113, 113, 0.4)";
            qDiv.style.background = isCorrect ? "rgba(74, 222, 128, 0.05)" : "rgba(248, 113, 113, 0.05)";

            let optsHtml = "";
            const safeOpts = Array.isArray(q.options) ? q.options : (typeof q.options === 'object' && q.options !== null ? Object.values(q.options) : [String(q.options || "")]);
            safeOpts.forEach((opt, oi) => {
                let color = "inherit";
                if (oi === ans) color = "#4ade80";
                else if (oi === sel && sel !== ans) color = "#f87171";
                optsHtml += `<div style="padding: 8px; margin-top: 4px; border-radius: 4px; background: rgba(0,0,0,0.2); color: ${color};">${oi === ans ? '✓ ' : (oi === sel ? '✗ ' : '')}${renderMarkdown(String(opt || "").replace(/<([a-zA-Z\/])/g, '&lt;$1')).replace(/^<p>|<\/p>\n?$/g, '')}</div>`;
            });

            qDiv.innerHTML = `
                <div style="font-weight: bold; margin-bottom: 10px;">Q${qi + 1}. ${renderMarkdown(String(q.question || "").replace(/<([a-zA-Z\/])/g, '&lt;$1'), true)}</div>
                ${optsHtml}
                <div class="expl-box"><strong>Explanation:</strong> ${renderMarkdown(q.explanation || "No explanation provided. (Try regenerating practice questions to fetch new AI explanations).", true)}</div>
            `;
            resultsDiv.appendChild(qDiv);
        });
        quizBody.appendChild(resultsDiv);
        btnSubmit.style.display = 'none';
        el("btnPrevQuiz")?.remove();
        el("btnNextQuiz")?.remove();

        const syllabusId = el("syllabusId")?.dataset?.id;
        if (syllabusId) {
          try {
            await api(BASE + `/api/syllabus/${encodeURIComponent(syllabusId)}/mcq/attempt`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                unit_index: state.unitIndex,
                topic_index: -1,
                question_count: total,
                score: score,
                time_seconds: elapsedSec,
              }),
            });
          } catch (err) {
            quizResult.textContent = `Result: ${correct}/${total}. (Could not save attempt: ${err.message})`;
          }
        }

        try {
          const syllabusId = el("syllabusId")?.dataset?.id;
          if (syllabusId) {
            const usage = await api(BASE + `/api/dashboard/usage/${encodeURIComponent(syllabusId)}`);
            if (usage && usage.attempt_summary) {
              attemptSummary = usage.attempt_summary;
              summaryByKey.clear();
              attemptSummary.forEach((s) => {
                if (!summaryByKey.has(s.unit_index)) {
                  summaryByKey.set(s.unit_index, { attempts: 0, best_score: 0 });
                }
                let current = summaryByKey.get(s.unit_index);
                current.attempts += s.attempts;
                if (s.best_score > current.best_score) current.best_score = s.best_score;
              });
              renderUnitList();
            }
          }
        } catch (_) {}
      };
    }

    if (btnRestart) {
      btnRestart.onclick = () => {
        startQuiz(state.unitIndex);
      };
    }
  }

  function renderFlashcards(studyJson, flashcardReviews) {
    const unitsEl = el("flashUnits");
    const mainEl = el("flashMain");
    if (!unitsEl || !mainEl) return;

    unitsEl.innerHTML = "";
    mainEl.innerHTML = "";

    flashcardReviews = flashcardReviews || {};

    const flashUnits =
      studyJson && studyJson.flashcards && studyJson.flashcards.units
        ? studyJson.flashcards.units
        : [];

    const unitNames = new Set();
    flashUnits.forEach((u) => unitNames.add(u.unit_name));
    const unitList = Array.from(unitNames).filter(Boolean);

    if (!unitList.length) {
      mainEl.innerHTML = `
        <div class="empty big">
          <div class="empty-icon"></div>
          <div class="empty-title">No flashcards yet</div>
        </div>
      `;
      return;
    }

    const flashByName = new Map(flashUnits.map((u) => [u.unit_name, u]));
    let currentFilter = "all";

    function activateUnit(name) {
      qsa(".side-item", unitsEl).forEach((n) => n.classList.toggle("active", n.dataset.unitName === name));

      const flash = flashByName.get(name) || { cards: [] };
      let cards = flash.cards || [];

      const now = new Date();
      if (currentFilter === "due") {
          cards = cards.filter(c => {
              const key = `${name}_${c.front}`;
              const nextReview = flashcardReviews[key];
              return !nextReview || new Date(nextReview) <= now;
          });
      }

      mainEl.innerHTML = `
        <div class="study-block">
          <div style="display: flex; justify-content: space-between; align-items: center;">
            <div>
              <div class="unit-title">${escapeHtml(name)}</div>
              <div class="mega">Spaced Repetition System</div>
            </div>
            <div style="background: rgba(0,0,0,0.2); border-radius: 6px; padding: 4px; display: flex; gap: 4px;">
                <button class="btn sm ${currentFilter === 'due' ? 'primary' : 'ghost'}" id="btnFilterDue">Due Cards</button>
                <button class="btn sm ${currentFilter === 'all' ? 'primary' : 'ghost'}" id="btnFilterAll">All Cards</button>
            </div>
          </div>

          <div class="hr"></div>

          ${cards.length === 0 ? `<div class="empty-sub" style="margin-top: 20px;">No cards found for this filter. You're all caught up! 🎉</div>` : `
          <div class="card panel" style="padding:14px; margin:0; background: rgba(255,255,255,.02);">
            <div class="cards-grid">
              ${cards.map((c, idx) => `
                <div class="flashcard" id="fc_${idx}">
                  <div class="flashcard-inner">
                    <div class="front"><div class="q" style="font-size:18px;">${renderMarkdown(c.front || "")}</div><div class="muted" style="margin-top:20px; font-size:12px;">Click to flip over</div></div>
                    <div class="back" style="display:flex; flex-direction:column;">
                        <div style="flex:1; overflow-y:auto;">${renderMarkdown(c.back || "")}</div>
                        <div class="srs-buttons" style="display:flex; gap:5px; margin-top:10px; border-top:1px solid rgba(255,255,255,0.1); padding-top:10px;">
                            <button class="btn sm srs-btn" style="flex:1; background:#ef4444;" data-q="again" data-idx="${idx}" data-front="${escapeHtml(c.front)}">Again</button>
                            <button class="btn sm srs-btn" style="flex:1; background:#f97316;" data-q="hard" data-idx="${idx}" data-front="${escapeHtml(c.front)}">Hard</button>
                            <button class="btn sm srs-btn" style="flex:1; background:#3b82f6;" data-q="good" data-idx="${idx}" data-front="${escapeHtml(c.front)}">Good</button>
                            <button class="btn sm srs-btn" style="flex:1; background:#10b981;" data-q="easy" data-idx="${idx}" data-front="${escapeHtml(c.front)}">Easy</button>
                        </div>
                    </div>
                  </div>
                </div>
              `).join("")}
            </div>
          </div>
          `}
        </div>
      `;
      triggerAnimation(mainEl);

      el("btnFilterDue")?.addEventListener("click", () => { currentFilter = "due"; activateUnit(name); });
      el("btnFilterAll")?.addEventListener("click", () => { currentFilter = "all"; activateUnit(name); });

      qsa(".flashcard", mainEl).forEach((card) => {
        card.addEventListener("click", (e) => {
          if (e.target.closest('.srs-buttons')) return;
          card.classList.toggle("flipped");
        });
      });

      qsa(".srs-btn", mainEl).forEach((btn) => {
          btn.addEventListener("click", async (e) => {
              e.stopPropagation();
              const quality = btn.dataset.q;
              const front = btn.dataset.front;
              const syllabusId = el("syllabusId")?.dataset?.id;

              btn.innerHTML = "...";
              try {
                  await api(BASE + `/api/syllabus/${encodeURIComponent(syllabusId)}/flashcard/review`, {
                      method: "POST", headers:{"Content-Type":"application/json"},
                      body: JSON.stringify({ unit_name: name, front_text: front, quality })
                  });
                  const cardEl = el(`fc_${btn.dataset.idx}`);
                  cardEl.style.opacity = '0.5';
                  cardEl.style.pointerEvents = 'none';
                  setToast("Review saved");

                  flashcardReviews[`${name}_${front}`] = new Date(Date.now() + 86400000).toISOString();

                  if (currentFilter === "due") {
                      setTimeout(() => activateUnit(name), 500);
                  }
              } catch(err) {
                  setToast(err.message);
                  btn.innerHTML = quality;
              }
        });
      });
    }

    unitList.forEach((name, i) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "side-item" + (i === 0 ? " active" : "");
      btn.dataset.unitName = name;

      const flash = flashByName.get(name) || { cards: [] };
      const now = new Date();
      let dueCount = 0;
      (flash.cards || []).forEach(c => {
          const key = `${name}_${c.front}`;
          if (!flashcardReviews[key] || new Date(flashcardReviews[key]) <= now) dueCount++;
      });

      btn.innerHTML = `<span>${escapeHtml(name)}</span> ${dueCount > 0 ? `<span class="pill sm" style="background:#ef4444; color:white;">${dueCount} Due</span>` : `<span class="pill sm" style="background:#10b981; color:white;">Done</span>`}`;

      btn.addEventListener("click", () => activateUnit(name));
      unitsEl.appendChild(btn);
    });

    activateUnit(unitList[0]);
  }

  function renderWritten(studyJson, syllabusId) {
    const unitsEl = el("writtenUnits");
    const mainEl = el("writtenMain");
    if (!unitsEl || !mainEl) return;

    unitsEl.innerHTML = "";
    mainEl.innerHTML = "";

    const studyUnits = studyJson && studyJson.study_units ? studyJson.study_units : [];
    const unitList = studyUnits.map((u) => u.unit_name).filter(Boolean);

    if (!unitList.length) {
      mainEl.innerHTML = `
        <div class="empty big">
          <div class="empty-icon"></div>
          <div class="empty-title">No written questions</div>
        </div>
      `;
      return;
    }

    const studyByName = new Map(studyUnits.map((u) => [u.unit_name, u]));

    function activateUnit(name) {
      qsa(".side-item", unitsEl).forEach((n) => n.classList.toggle("active", n.dataset.unitName === name));
      const stud = studyByName.get(name) || {};

      const shortQs = stud.short_questions || [];
      const mediumQs = stud.medium_questions || [];
      const longQs = stud.long_questions || [];

      function createQAHtml(qList, typePrefix) {
          return qList.slice(0, 20).map((x, xi) => `
            <div class="qa" style="margin-bottom:10px;" data-type="${typePrefix}" data-idx="${xi}">
              <div class="q">${renderMarkdown(x.q || x.question || "")}</div>
              <div class="a answer-block" style="display:none;">${renderMarkdown(x.a || x.answer || "")}</div>
              <div class="practice-block" style="margin-top:10px;">
                  <button class="btn ghost sm btn-view-ans">View Answer</button>
                  <button class="btn ghost sm btn-practice-ai">Practice with AI</button>
              </div>
              <div class="practice-area" style="display:none; margin-top:10px;"></div>
            </div>
          `).join("");
      }

      mainEl.innerHTML = `
        <div class="study-block">
          <div class="unit-title" style="margin-bottom:20px;">${escapeHtml(name)}</div>
          <div class="qtype-grid">
            <div class="mega" style="font-weight:800; margin-top:0;">Short Questions</div>
            ${createQAHtml(shortQs, 'short')}

            <div class="mega" style="font-weight:800; margin-top:20px;">Medium Questions</div>
            ${createQAHtml(mediumQs, 'medium')}

            <div class="mega" style="font-weight:800; margin-top:20px;">Long Questions</div>
            ${createQAHtml(longQs, 'long')}
          </div>
        </div>
      `;
      triggerAnimation(mainEl);

      qsa('.qa', mainEl).forEach(qaEl => {
          const btnView = qs('.btn-view-ans', qaEl);
          const btnPrac = qs('.btn-practice-ai', qaEl);
          const ansBlock = qs('.answer-block', qaEl);
          const pracBlock = qs('.practice-block', qaEl);
          const pracArea = qs('.practice-area', qaEl);

          const type = qaEl.dataset.type;
          const idx = qaEl.dataset.idx;

          let qObj;
          if (type === 'short') qObj = shortQs[idx];
          else if (type === 'medium') qObj = mediumQs[idx];
          else if (type === 'long') qObj = longQs[idx];

          if (btnView) {
              btnView.onclick = () => {
                  ansBlock.style.display = 'block';
                  btnView.style.display = 'none';
              };
          }

          if (btnPrac) {
              btnPrac.onclick = () => {
                  pracBlock.style.display = 'none';
                  pracArea.style.display = 'block';
                  pracArea.innerHTML = `
                      <textarea placeholder="Type your answer here..." style="height:100px; margin-bottom:10px;"></textarea>
                      <div class="processing-label" style="display:none; color:#3b82f6; font-weight:bold; margin-bottom:10px;">Evaluating your answer... <span class="spinner sm" style="display:inline-block"></span></div>
                      <button class="btn primary sm btn-submit-eval">Evaluate</button>
                      <button class="btn ghost sm btn-cancel-eval">Cancel</button>
                      <div class="eval-result" style="margin-top:10px;"></div>
                  `;

                  qs('.btn-cancel-eval', pracArea).onclick = () => {
                      pracArea.style.display = 'none';
                      pracBlock.style.display = 'block';
                  };

                  qs('.btn-submit-eval', pracArea).onclick = async (e) => {
                      const btn = e.target;
                      const ans = qs("textarea", pracArea).value.trim();
                      if (!ans) return setToast("Please type an answer first.");

                      const procLabel = qs('.processing-label', pracArea);
                      const cancelBtn = qs('.btn-cancel-eval', pracArea);
                      const textArea = qs("textarea", pracArea);

                      btn.style.display = 'none';
                      cancelBtn.style.display = 'none';
                      procLabel.style.display = 'block';
                      textArea.disabled = true;

                      try {
                          const res = await api(BASE + '/api/evaluate_written', {
                              method:'POST',
                              headers:{'Content-Type':'application/json'},
                              body:JSON.stringify({
                                  question: qObj.q || qObj.question,
                                  actual_answer: qObj.a || qObj.answer,
                                  user_answer: ans
                              })
                          });
                          procLabel.style.display = 'none';
                          qs(".eval-result", pracArea).innerHTML = `
                              <div class="well">
                                  <h4 style="margin:0 0 10px 0; color:#3b82f6;">Score: ${res.evaluation.score_out_of_10} / 10</h4>
                                  <p style="margin:0;">${renderMarkdown(res.evaluation.feedback)}</p>
                              </div>
                              <div style="margin-top:15px; border-top:1px solid rgba(255,255,255,0.1); padding-top:10px;">
                                  <strong style="color:#4ade80;">Reference AI Answer:</strong>
                                  <div style="margin-top:5px;">${renderMarkdown(qObj.a || qObj.answer)}</div>
                              </div>
                          `;
                      } catch (err) {
                          btn.style.display = 'inline-block';
                          cancelBtn.style.display = 'inline-block';
                          procLabel.style.display = 'none';
                          textArea.disabled = false;
                          setToast(err.message);
                      }
                  };
              };
          }
      });
    }

    unitList.forEach((name, i) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "side-item" + (i === 0 ? " active" : "");
      btn.dataset.unitName = name;
      btn.textContent = name;
      btn.addEventListener("click", () => activateUnit(name));
      unitsEl.appendChild(btn);
    });

    activateUnit(unitList[0]);
  }

  // ✅ Correct escaping to prevent HTML injection + keep JS valid
  function escapeHtml(s) {
    if (s === null || s === undefined) return "";
    const str = String(s);
    return str.replace(/&/g, "&amp;")
              .replace(/</g, "&lt;")
              .replace(/>/g, "&gt;")
              .replace(/"/g, "&quot;")
              .replace(/'/g, "&#039;");
  }

  function renderExtraNotes(extraNotesJson, extraMaterials, refreshCallback) {
    const mainEl = el("extraMain");
    const materialsList = el("extraMaterialsList");
    if (!mainEl || !materialsList) return;

    materialsList.innerHTML = "";
    (extraMaterials || []).forEach(m => {
        const div = document.createElement("div");
        div.className = "side-item";
        div.style.cursor = "default";
        div.innerHTML = `
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <span style="font-size: 0.9em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 150px;">
                    ${m.material_type === 'image' ? 'Image:' : (m.material_type === 'pdf' ? 'PDF:' : (m.material_type === 'docx' ? 'Word:' : 'Note:'))}
                    ${escapeHtml(m.original_name || 'Pasted Text')}
                </span>
                ${m.url ? `<a href="${m.url}" target="_blank" style="color: #3b82f6; font-size: 0.8em; text-decoration: none; margin-left: 10px;">View</a>` : ''}
            </div>
        `;
        materialsList.appendChild(div);
    });

    function showDefaultView() {
        if (!extraNotesJson || !extraNotesJson.notes) {
            mainEl.innerHTML = `<div class="empty big card panel"><div class="empty-icon"></div><div class="empty-title">Extra Notes</div><div class="empty-sub">Add materials on the left, then generate notes.</div><button class="btn primary" id="btnGenerateExtra" style="margin-top: 15px;">Generate Extra Notes</button></div>`;
        } else {
            let topicsHtml = "";
            (extraNotesJson.topics || []).forEach(t => {
                topicsHtml += `<div class="qa" style="margin-top: 15px;"><div class="q">${renderMarkdown(t.topic_name || "")}</div><div class="a">${renderMarkdown(t.topic_notes || "")}</div></div>`;
            });
            mainEl.innerHTML = `<div class="card panel animate-in" style="padding: 20px;"><div class="unit-title">Extra Notes Summary</div><div class="mega" style="margin-bottom:20px">${renderMarkdown(extraNotesJson.notes || "")}</div><button class="btn ghost" id="btnGenerateExtra" style="margin-bottom: 15px;">Regenerate Notes</button><div class="hr"></div>${topicsHtml}</div>`;
        }

        el("btnGenerateExtra")?.addEventListener("click", async () => {
            const syllabusId = el("syllabusId")?.dataset?.id;
            if (!syllabusId) return;
            const btn = el("btnGenerateExtra");
            btn.disabled = true;
            btn.textContent = "Generating...";
            try {
                await api(BASE + `/api/syllabus/${encodeURIComponent(syllabusId)}/extra/generate`, { method: "POST" });
                setToast("Extra notes generated!");
                if (refreshCallback) refreshCallback();
                window.refreshUserCredits();
            } catch (err) {
                setToast(err.message);
                btn.disabled = false;
                btn.textContent = "Try Again";
            }
        });
    }

    showDefaultView();

    el("btnAddExtraMaterial")?.addEventListener("click", () => {
        mainEl.innerHTML = `
            <div class="card panel animate-in" style="padding: 20px;">
                <h3>Add Extra Material</h3>
                <form id="extraUploadForm" style="display: flex; flex-direction: column; gap: 15px; margin-top: 15px;">
                    <div><label class="muted" style="display:block; margin-bottom:5px;">File (PDF, Word, or Image, max 5MB)</label><input type="file" name="file" accept=".pdf,.docx,image/*" class="neumorphic-input"></div>
                    <div><label class="muted" style="display:block; margin-bottom:5px;">Or type a topic/text</label><textarea name="text_content" rows="4" class="neumorphic-input" style="resize: vertical;"></textarea></div>
                    <div style="display:flex; gap: 10px; margin-top: 10px;"><button type="button" class="btn ghost" id="btnCancelExtra">Cancel</button><button type="submit" class="btn primary" id="btnSubmitExtra">Upload</button></div>
                </form>
            </div>
        `;
        el("btnCancelExtra").addEventListener("click", showDefaultView);
        el("extraUploadForm").addEventListener("submit", async (e) => {
            e.preventDefault();
            const fileInp = qs('input[type="file"]', e.target);
            if (fileInp && fileInp.files && fileInp.files[0] && fileInp.files[0].size > 5 * 1024 * 1024) {
                return setToast("File size exceeds 5MB limit.");
            }
            const btn = el("btnSubmitExtra"); btn.disabled = true; btn.textContent = "Uploading...";
            const syllabusId = el("syllabusId")?.dataset?.id;
            try {
                const res = await fetch(BASE + `/api/syllabus/${encodeURIComponent(syllabusId)}/extra/upload`, { method: "POST", body: new FormData(e.target) });
                const data = await res.json();
                if (!res.ok || !data.ok) throw new Error(data.error || "Upload failed");
                setToast("Uploaded successfully");
                if (refreshCallback) refreshCallback();
            } catch (err) { setToast(err.message); btn.disabled = false; btn.textContent = "Upload"; }
        });
    });
  }

  // ------------------------
  // Podcast Engine & Page
  // ------------------------
  class PremiumTTS {
      constructor() {
          this.synth = window.speechSynthesis;
          this.voiceA = null; // Female / Host 1
          this.voiceB = null; // Male / Host 2
          this.onBoundary = null;
          this.onEnd = null;

          if (!this.synth) return;

          try {
              this.loadVoices();
              if (this.synth.onvoiceschanged !== undefined) {
                  this.synth.onvoiceschanged = () => this.loadVoices();
              }
          } catch (e) {
              console.warn("TTS Initialization error:", e);
          }
      }

      loadVoices() {
          if (!this.synth) return;
          const voices = this.synth.getVoices();
          if (!voices.length) return;

          // Try to find the absolute best, most natural voices in Chrome/Edge
          const premiumFemale = voices.find(v => (v.name.includes("Google") || v.name.includes("Online")) && (v.name.includes("Female") || v.name.includes("Aria") || v.name.includes("Jenny") || v.name.includes("Zira"))) || voices.find(v => v.name.includes("Female"));
          const premiumMale = voices.find(v => (v.name.includes("Google") || v.name.includes("Online")) && (v.name.includes("Male") || v.name.includes("Guy") || v.name.includes("Christopher"))) || voices.find(v => v.name.includes("Male"));

          this.voiceA = premiumFemale || voices[0];
          this.voiceB = premiumMale || voices[1] || voices[0];
      }

      speak(text, speaker, emotion) {
          this.synth.cancel(); // Stop anything playing
          const utter = new SpeechSynthesisUtterance(text);

          utter.voice = speaker === "Alex" ? this.voiceA : this.voiceB;

          // Tweak params to simulate emotion (Very human-like hack)
          utter.pitch = 1.0;
          utter.rate = 1.0;

          if (emotion === "excited") {
              utter.pitch = 1.2;
              utter.rate = 1.1;
          } else if (emotion === "serious") {
              utter.pitch = 0.9;
              utter.rate = 0.9;
          }

          if (text.includes("!")) {
              utter.pitch = Math.min(2.0, utter.pitch + 0.1);
              utter.volume = 1.0;
          }
          if (text.includes("?")) {
              utter.pitch = Math.min(2.0, utter.pitch + 0.05);
          }

          utter.onend = () => { if (this.onEnd) this.onEnd(); };

          this.synth.speak(utter);
      }

      stop() { this.synth.cancel(); }
      pause() { this.synth.pause(); }
      resume() { this.synth.resume(); }
  }

  async function wirePodcastPage() {
      const syllabusId = el("syllabusId")?.dataset?.id;
      if (!syllabusId) return;

      const unitsListEl = el("podcastUnitsList");
      if (!unitsListEl) return; // Not on podcast page

      let units = [];
      let storedPodcasts = {};

      try {
          const [unitsData, fullData] = await Promise.all([
              api(BASE + `/api/syllabus/${encodeURIComponent(syllabusId)}/units`),
              api(BASE + `/api/syllabus/${encodeURIComponent(syllabusId)}/data`)
          ]);
          units = unitsData.units || [];
          storedPodcasts = fullData.podcast || {};
      } catch (err) {
          unitsListEl.innerHTML = `<div class="empty-sub" style="color: #ef4444;">Error loading units: ${err.message}</div>`;
          return;
      }

      unitsListEl.innerHTML = "";
      if (!units.length) {
          unitsListEl.innerHTML = `<div class="empty-sub">No units found.</div>`;
          return;
      }

      let currentScript = [];
      let currentIdx = 0;
      let isPlaying = false;
      let activeUnitName = null;

      let tts;
      try {
          tts = new PremiumTTS();
      } catch(e) {
          console.warn("TTS Engine failed:", e);
          tts = { speak: ()=>{}, stop: ()=>{}, pause: ()=>{}, resume: ()=>{}, synth: {} };
      }

      function showScreen(screenId) {
          ["podcastEmptyState", "podcastGenerateScreen", "podcastLoader", "podcastPlayerScreen"].forEach(id => {
              const scr = el(id);
              if (scr) scr.hidden = (id !== screenId);
          });
      }

      units.forEach((u, i) => {
          const btn = document.createElement("button");
          btn.type = "button";
          btn.className = "side-item" + (i === 0 ? " active" : "");
          btn.dataset.unitName = u;

          const hasPod = !!storedPodcasts[u];
          btn.innerHTML = `<span>${escapeHtml(u)}</span> ${hasPod ? '<span class="pill sm" style="background:var(--accent); color:white;">Ready</span>' : ''}`;

          btn.addEventListener("click", () => activateUnit(u));
          unitsListEl.appendChild(btn);
      });

      function activateUnit(name) {
          tts.stop();
          isPlaying = false;

          qsa(".side-item", unitsListEl).forEach(n => n.classList.toggle("active", n.dataset.unitName === name));
          activeUnitName = name;

          const hasPod = !!storedPodcasts[name];
          if (hasPod) {
              loadScript(storedPodcasts[name].script);
          } else {
              el("pgUnitName").textContent = name;
              showScreen("podcastGenerateScreen");
          }
      }

      el("btnGeneratePodcast")?.addEventListener("click", async () => {
          if (!activeUnitName) return;
          try {
              showScreen("podcastLoader");
              const res = await api(BASE + `/api/syllabus/${encodeURIComponent(syllabusId)}/podcast/generate`, {
                  method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({ unit_name: activeUnitName })
              });
              storedPodcasts[activeUnitName] = res; // cache

              const activeBtn = Array.from(unitsListEl.children).find(b => b.dataset.unitName === activeUnitName);
              if (activeBtn) activeBtn.innerHTML = `<span>${escapeHtml(activeUnitName)}</span> <span class="pill sm" style="background:var(--accent); color:white;">Ready</span>`;

              window.refreshUserCredits();
              loadScript(res.script);
          } catch (err) {
              setToast(err.message);
              showScreen("podcastGenerateScreen");
          }
      });

      function loadScript(scriptArr) {
          currentScript = scriptArr;
          currentIdx = 0;

          showScreen("podcastPlayerScreen");

          el("podSlider").max = Math.max(0, currentScript.length - 1);

          const tBox = el("transcriptBox");
          tBox.innerHTML = currentScript.map((line, i) => `
              <div class="transcript-line" id="tline_${i}" data-idx="${i}">
                  <div class="speaker-name speaker-${escapeHtml(line.speaker)}">${escapeHtml(line.speaker)}</div>
                  <div>${escapeHtml(line.text)}</div>
              </div>
          `).join("");

          qsa(".transcript-line", tBox).forEach(div => {
              div.addEventListener("click", () => {
                  currentIdx = parseInt(div.dataset.idx, 10);
                  playLine();
              });
          });

          updateUI();
      }

      function updateUI() {
          qsa(".transcript-line").forEach((el, i) => {
              if (i === currentIdx) {
                  el.classList.add("active");
                  el.scrollIntoView({ behavior: 'smooth', block: 'center' });
              } else {
                  el.classList.remove("active");
              }
          });

          el("podSlider").value = currentIdx;
          el("podProgressLabel").textContent = currentScript.length > 0 ? `${currentIdx + 1} / ${currentScript.length}` : "0 / 0";

          const curLine = currentScript[currentIdx];
          el("currentSpeaker").textContent = curLine ? `🗣️ ${curLine.speaker} is speaking...` : "Finished";

          const mic = el("micIcon");
          if (isPlaying) mic.classList.add("active"); else mic.classList.remove("active");
          el("btnPodPlayPause").innerHTML = isPlaying ? "⏸" : "▶";
      }

      function playLine() {
          if (currentIdx >= currentScript.length) {
              isPlaying = false;
              updateUI();
              return;
          }
          isPlaying = true;
          updateUI();

          const line = currentScript[currentIdx];
          tts.onEnd = () => {
              currentIdx++;
              if (isPlaying) playLine();
          };
          tts.speak(line.text, line.speaker, line.emotion);
      }

      el("btnPodPlayPause").addEventListener("click", () => {
          if (isPlaying) {
              isPlaying = false;
              tts.pause();
              updateUI();
          } else {
              if (tts.synth.paused) {
                  isPlaying = true;
                  tts.resume();
                  updateUI();
              } else {
                  playLine();
              }
          }
      });

      el("btnPodStop").addEventListener("click", () => {
          isPlaying = false;
          tts.stop();
          currentIdx = 0;
          updateUI();
      });

      el("btnPodNext").addEventListener("click", () => {
          if (currentIdx < currentScript.length - 1) {
              currentIdx++;
              if (isPlaying) playLine(); else updateUI();
          }
      });

      el("btnPodPrev").addEventListener("click", () => {
          if (currentIdx > 0) {
              currentIdx--;
              if (isPlaying) playLine(); else updateUI();
          }
      });

      el("podSlider").addEventListener("input", (e) => {
          currentIdx = parseInt(e.target.value, 10);
          updateUI();
      });
      el("podSlider").addEventListener("change", () => {
          if (isPlaying) playLine();
      });

      // Stop audio on page leave
      window.addEventListener("beforeunload", () => tts.stop());

      // Auto-select first unit
      if (units.length > 0) activateUnit(units[0]);
  }

  async function wireSyllabusPage() {
    const syllabusIdEl = el("syllabusId");
    if (!syllabusIdEl) return;

    const syllabusId = syllabusIdEl.dataset.id;
    const btnGenerateAll = el("btnGenerateAll");
    if (!btnGenerateAll) return; // Prevent heavy rendering scripts on the podcast page

    setupTTS();

    const btnRefreshNotes = el("btnRefreshNotes");
    const btnRefreshPractice = el("btnRefreshPractice");

    async function loadData() {
      const data = await api(BASE + `/api/syllabus/${encodeURIComponent(syllabusId)}/data`);
      return data;
    }

    async function loadPracticeUsage() {
      try {
        const usage = await api(BASE + `/api/dashboard/usage/${encodeURIComponent(syllabusId)}`);
        return usage && usage.attempt_summary ? usage.attempt_summary : [];
      } catch (_) {
        return [];
      }
    }

    async function refreshNotes() {
      try {
        const data = await loadData();
        renderNotes(data.notes, syllabusId, refreshNotes);
      } catch (err) {
        setToast(err.message || String(err));
      }
    }

    async function refreshPractice() {
      try {
        const data = await loadData();
        const usage = await loadPracticeUsage();
        renderPractice(data.practice, usage);
      } catch (err) {
        setToast(err.message || String(err));
      }
    }

    async function refreshStudy() {
      try {
        const data = await loadData();
        renderFlashcards(data.study, data.flashcard_reviews);
        renderWritten(data.study, syllabusId);
      } catch (err) {
        setToast(err.message || String(err));
      }
    }

    async function refreshExtra() {
      try {
        const data = await loadData();
        renderExtraNotes(data.extra_notes, data.extra_materials, refreshExtra);
      } catch (err) {
        setToast(err.message || String(err));
      }
    }

    function setGenerating(btn, generating) {
      if (!btn) return;
      btn.disabled = generating;
    }

    function setProgressUI(show, text, percent) {
      const modal = el("progressModal");
      const pText = el("progressText");
      const pBar = el("progressBar");
      const pPercent = el("progressPercent");
      if (!modal) return;
      if (show) {
        modal.classList.remove("fade-out");
        modal.hidden = false;
        if (text) pText.textContent = text;
        if (percent !== undefined) {
          pBar.style.width = percent + "%";
          pPercent.textContent = Math.round(percent) + "%";
        }
      } else {
        modal.classList.add("fade-out");
        setTimeout(() => { if (modal.classList.contains("fade-out")) modal.hidden = true; }, 500);
      }
    }

    if (btnGenerateAll) {
      btnGenerateAll.addEventListener("click", async () => {
        try {
          btnGenerateAll.disabled = true;
          const spinner = qs("#btnGenerateAll .spinner");
          if (spinner) spinner.hidden = false;

          setProgressUI(true, "Checking credits & analyzing units...", 0);

          const userRes = await api(BASE + '/api/user/me');
          const credits = userRes.credits || 0;

          const unitsRes = await api(BASE + `/api/syllabus/${encodeURIComponent(syllabusId)}/units`);
          let unitsToProcess = unitsRes.units || [];

          if (userRes.role !== 'admin') {
              if (credits <= 0) {
                  alert("0 credits left. Contact admin for credits, admin email : baredamantra@gmail.com");
                  setProgressUI(false);
                  return window.location.href = BASE + "/dashboard";
              }
              if (credits < unitsToProcess.length) {
                  if (!confirm(`You only have ${credits} credits, but ${unitsToProcess.length} units are detected. Generate content for the first ${credits} units?`)) {
                      setProgressUI(false);
                      return;
                  }
                  unitsToProcess = unitsToProcess.slice(0, credits);
              }
          }

          for (let i = 0; i < unitsToProcess.length; i++) {
              const u = unitsToProcess[i];
              const baseProg = (i / unitsToProcess.length) * 100;
              const progChunk = (1 / unitsToProcess.length) * 100;

              setProgressUI(true, `Processing ${u}...`, baseProg);

              await fetchStreaming(BASE + `/api/syllabus/${encodeURIComponent(syllabusId)}/generate_unit`, {
                  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ unit_name: u })
              }, (data) => {
                  if (data.status === "error") throw new Error(data.message || "Generation failed");
                  if (data.status === "progress") setProgressUI(true, `${u}: ${data.message}`, baseProg + ((data.progress||0) / 100) * progChunk);
              });
          }

          await refreshNotes();
          await refreshPractice();
          await refreshStudy();

          setProgressUI(true, "All Done!", 100);
          setToast("All content generated");
        } catch (err) {
          setToast(err.message || String(err));
        } finally {
          btnGenerateAll.disabled = false;
          const spinner = qs("#btnGenerateAll .spinner");
          if (spinner) spinner.hidden = true;
          setTimeout(() => setProgressUI(false), 2000);
          window.refreshUserCredits();
        }
      });
    }

    if (btnRefreshNotes) btnRefreshNotes.addEventListener("click", refreshNotes);
    if (btnRefreshPractice) btnRefreshPractice.addEventListener("click", refreshPractice);
    const btnRefreshExtra = el("btnRefreshExtra");
    if (btnRefreshExtra) btnRefreshExtra.addEventListener("click", refreshExtra);

    await refreshNotes();
    await refreshPractice();
    await refreshStudy();
    await refreshExtra();

    // Auto-generate contents logic if initially empty
    try {
        const initialData = await loadData();
        const hasNotes = initialData.notes && initialData.notes.units && initialData.notes.units.length > 0;
        if (!hasNotes && btnGenerateAll && !btnGenerateAll.disabled) {
            console.log("No content detected, triggering initial auto-generation...");
            setTimeout(() => btnGenerateAll.click(), 600);
        }
    } catch (e) { console.error("Auto-start check failed", e); }

    // Setup PDF Export

    el("btnExportPDF")?.addEventListener("click", async () => {
        const syllabusId = syllabusIdEl.dataset.id;

        let loader = el("pdfLoaderOverlay");
        if (!loader) {
            loader = document.createElement("div");
            loader.id = "pdfLoaderOverlay";
            loader.style.cssText = "position:fixed; top:0; left:0; right:0; bottom:0; background:rgba(0,0,0,0.85); z-index:99999; display:flex; flex-direction:column; justify-content:center; align-items:center; color:#fff;";
            loader.innerHTML = `
                <style>
                    @keyframes pulseText {
                        0% { opacity: 0.7; }
                        50% { opacity: 1; }
                        100% { opacity: 0.7; }
                    }
                    @keyframes progressIndeterminate {
                        0% { left: -50%; width: 50%; }
                        50% { left: 25%; width: 50%; }
                        100% { left: 100%; width: 50%; }
                    }
                </style>
                <div style="font-size: 24px; font-weight: bold; display: flex; flex-direction: column; align-items: center; gap: 15px;">
                    <img src="/static/loading_animation.gif" alt="Loading" style="width: 150px; border-radius: 12px;">
                    <div style="animation: pulseText 1.5s ease-in-out infinite; color: #60a5fa;">Crafting your beautiful PDF...</div>
                    <div style="width: 200px; height: 6px; background: rgba(255,255,255,0.2); border-radius: 3px; overflow: hidden; position: relative;">
                        <div style="position: absolute; top: 0; left: 0; height: 100%; background: #60a5fa; border-radius: 3px; animation: progressIndeterminate 1.5s infinite ease-in-out;"></div>
                    </div>
                    <div style="font-size: 14px; font-weight: normal; color: #9ca3af;">Using python rendering engine, this might take a moment.</div>
                </div>
            `;
            document.body.appendChild(loader);
        }
        loader.style.display = "flex";

        try {
            const metaRes = await api(BASE + `/api/syllabus/${encodeURIComponent(syllabusId)}/meta`);
            const data = await api(BASE + `/api/syllabus/${encodeURIComponent(syllabusId)}/data`);

            const courseName = metaRes.meta?.course_name || "Study Material";
            const className = metaRes.meta?.class_name || "";

            const container = document.createElement("div");

            let html = `
                <style>
                    .pdf-header { text-align: center; font-size: 14px; color: #3b82f6; font-weight: bold; margin-bottom: 20px; }
                    .pdf-content { font-family: Helvetica, Arial, sans-serif; color: #333; line-height: 1.5; font-size: 12px; }
                    .pdf-content h1 { font-size: 24px; color: #111827; text-align: center; margin-bottom: 5px; }
                    .pdf-content .class-name { text-align: center; color: #6b7280; margin-bottom: 30px; font-size: 14px; }
                    .pdf-content h2 { font-size: 18px; color: #1f2937; margin-top: 20px; }
                    .pdf-content h3 { font-size: 14px; color: #374151; margin-top: 15px; }
                    .pdf-content h4 { font-size: 13px; color: #4b5563; margin-top: 10px; }
                    .pdf-content h3, .pdf-content h4 { page-break-after: avoid; }
                    .pdf-content p { margin-bottom: 8px; }
                    .pdf-content pre { background-color: #f3f4f6; padding: 10px; border: 1px solid #e5e7eb; border-radius: 4px; font-family: Courier, monospace; font-size: 10px; white-space: pre-wrap; }
                    .pdf-content code { font-family: Courier, monospace; background-color: #f3f4f6; padding: 2px 4px; color: #ef4444; border-radius: 3px; font-size: 10px; }
                    .pdf-content ul, .pdf-content ol { margin-bottom: 10px; padding-left: 20px; }
                    .pdf-content .qa-box { border: 1px solid #e5e7eb; padding: 10px; margin-bottom: 15px; background-color: #f9fafb; border-radius: 6px; page-break-inside: avoid; }
                    .pdf-content .unit-title { background-color: #f3f4f6; padding: 10px; border-left: 4px solid #3b82f6; margin-top: 20px; font-weight: bold; font-size: 16px; border-radius: 4px; }
                    .pdf-content .unit-title { background-color: #f3f4f6; color: #000000; padding: 10px; border-left: 4px solid #3b82f6; margin-top: 20px; font-weight: bold; font-size: 16px; border-radius: 4px; }
                    .pdf-content .page-break { page-break-before: always; }
                </style>
                <div class="pdf-content">
                    <div class="pdf-header">Sage AI</div>
                    <h1>${escapeHtml(courseName)}</h1>
                    <div class="class-name">${escapeHtml(className)}</div>
            `;

            const notes = data.notes?.units || [];
            const study_units = data.study?.study_units || [];

            const unitNames = new Set();
            [notes, study_units].forEach(arr => {
                arr.forEach(u => { if (u.unit_name) unitNames.add(u.unit_name); });
            });

            Array.from(unitNames).forEach((uname, i) => {
                if (i > 0) html += `<div class="page-break"></div>`;
                html += `<div class="unit-title">Unit ${i+1}: ${escapeHtml(uname)}</div>`;

                const n_unit = notes.find(u => u.unit_name === uname);
                if (n_unit) {
                    html += `<h3>Notes</h3><div>${renderMarkdown(n_unit.notes || "")}</div>`;
                    (n_unit.topics || []).forEach(t => {
                        html += `<h4>${renderMarkdown(t.topic_name || "")}</h4><div>${renderMarkdown(t.topic_notes || "")}</div>`;
                    });
                }

                const s_unit = study_units.find(u => u.unit_name === uname);
                if (s_unit) {
                    const hasWritten = ['short_questions', 'medium_questions', 'long_questions'].some(k => s_unit[k] && s_unit[k].length > 0);
                    if (hasWritten) {
                        html += `<h3>Written Questions</h3>`;
                        [ ['short_questions', 'Short'], ['medium_questions', 'Medium'], ['long_questions', 'Long'] ].forEach(([k, title]) => {
                            const qs = s_unit[k] || [];
                            if (qs.length > 0) {
                                html += `<h4>${title} Questions</h4>`;
                                qs.forEach(q => {
                                    html += `<div class="qa-box">
                                        <div style="margin-bottom:5px;"><strong>Q.</strong> ${renderMarkdown(q.q || q.question || "").replace(/^<p>|<\/p>\n?$/g, '')}</div>
                                        <div style="padding-left:10px; border-left:2px solid #d1d5db;"><strong>A.</strong> ${renderMarkdown(q.a || q.answer || "").replace(/^<p>|<\/p>\n?$/g, '')}</div>
                                    </div>`;
                                });
                            }
                        });
                    }
                }
            });

            html += `</div>`;
            container.innerHTML = html;

            const wrapper = document.createElement("div");
            wrapper.style.position = "absolute";
            wrapper.style.left = "-9999px";
            wrapper.style.top = "-9999px";
            wrapper.appendChild(container);
            document.body.appendChild(wrapper);

            const opt = {
                margin:       0.5,
                filename:     `${courseName.replace(/[^a-zA-Z0-9]/g, '_')}_Study_Material.pdf`,
                image:        { type: 'jpeg', quality: 0.98 },
                html2canvas:  { scale: 2, useCORS: true, letterRendering: true },
                jsPDF:        { unit: 'in', format: 'a4', orientation: 'portrait' }
            };

            await html2pdf().set(opt).from(container).save();
            document.body.removeChild(wrapper);
            setToast("PDF Downloaded successfully!");

        } catch (err) {
            console.error(err);
            setToast("Error generating PDF: " + err.message);
        } finally {
            if (loader) loader.style.display = "none";
        }
    });

    const actionsBar = el("btnExportPDF")?.parentNode;
    if (actionsBar && !el("btnCheatSheet")) {
        const btnCS = document.createElement("button");
        btnCS.id = "btnCheatSheet";
        btnCS.className = "btn primary";
        btnCS.innerHTML = "Cheat Sheet";
        actionsBar.insertBefore(btnCS, el("btnExportPDF"));

        btnCS.addEventListener("click", async () => {
            let data = await loadData();
            if (!data.cheat_sheet) {
                let loader = el("cheatSheetLoaderOverlay");
                if (!loader) {
                    loader = document.createElement("div");
                    loader.id = "cheatSheetLoaderOverlay";
                    loader.style.cssText = "position:fixed; top:0; left:0; right:0; bottom:0; background:rgba(0,0,0,0.85); z-index:99999; display:flex; flex-direction:column; justify-content:center; align-items:center; color:#fff;";
                    loader.innerHTML = `
                        <style>
                            @keyframes progressIndeterminate {
                                0% { left: -50%; width: 50%; }
                                50% { left: 25%; width: 50%; }
                                100% { left: 100%; width: 50%; }
                            }
                        </style>
                        <div style="display: flex; flex-direction: column; align-items: center; gap: 15px;">
                            <img src="/static/loading_animation.gif" alt="Loading" style="width: 150px; border-radius: 12px;">
                            <div style="animation: pulseText 1.5s ease-in-out infinite; color: #ec4899; font-weight: bold; font-size: 18px;">Crafting your Cheat Sheet...</div>
                            <div style="width: 200px; height: 6px; background: rgba(255,255,255,0.2); border-radius: 3px; overflow: hidden; position: relative;">
                                <div style="position: absolute; top: 0; left: 0; height: 100%; background: #ec4899; border-radius: 3px; animation: progressIndeterminate 1.5s infinite ease-in-out;"></div>
                            </div>
                        </div>
                    `;
                    document.body.appendChild(loader);
                }
                loader.style.display = "flex";

                const btn = el("btnCheatSheet");
                const origText = btn.innerHTML;
                btn.disabled = true;
                try {
                    const res = await api(BASE + `/api/syllabus/${encodeURIComponent(syllabusId)}/generate/summary`, { method: "POST" });
                    data.cheat_sheet = res.cheat_sheet;
                    setToast("Cheat sheet generated!");
                    window.refreshUserCredits();
                } catch (e) {
                    setToast(e.message);
                    btn.disabled = false;
                    if (loader) loader.style.display = "none";
                    return;
                }
                btn.disabled = false;
                if (loader) loader.style.display = "none";
            }

            let modal = el("cheatSheetModal");
            if (!modal) {
                modal = document.createElement("div");
                                    modal.id = "cheatSheetModal";
                    modal.className = "animate-in";
                    modal.style.cssText = "position:fixed; top:0; left:0; right:0; bottom:0; background:rgba(0,0,0,0.6); backdrop-filter:blur(4px); z-index:100000; display:flex; justify-content:center; align-items:center; padding: 20px;";
                    modal.innerHTML = `
                        <style>
                            .cs-preview-wrapper {
                                font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
                                line-height: 1.6;
                            }
                            .cs-preview-wrapper h2 {
                                color: #4338ca;
                                border-bottom: 2px solid #e0e7ff;
                                padding-bottom: 8px;
                                margin-top: 25px;
                            }
                            .cs-preview-wrapper h3 {
                                color: #be185d;
                                margin-top: 20px;
                            }
                            .cs-preview-wrapper strong {
                                background-color: rgba(254, 240, 138, 0.4);
                                padding: 2px 4px;
                                border-radius: 3px;
                                color: #111827;
                            }
                            .cs-preview-wrapper blockquote {
                                border-left: 4px solid #3b82f6;
                                background-color: #eff6ff;
                                padding: 12px 20px;
                                margin: 15px 0;
                                border-radius: 0 8px 8px 0;
                                color: #1e3a8a;
                                font-style: italic;
                            }
                            .cs-preview-wrapper code {
                                background-color: #f3f4f6;
                                color: #e11d48;
                                padding: 2px 4px;
                                border-radius: 4px;
                            }
                        </style>
                        <div class="card" style="width:100%; max-width:800px; height:90vh; padding:0; display:flex; flex-direction:column; overflow:hidden;">
                            <div style="padding:20px; border-bottom:1px solid var(--border-color); display:flex; justify-content:space-between; align-items:center;">
                                <h2 style="margin:0;">Final Review Cheat Sheet</h2>
                                <div style="display:flex; gap:10px;">
                                    <button class="btn primary sm" id="btnDownloadCS">Download PDF</button>
                                    <button class="btn ghost sm" onclick="this.closest('#cheatSheetModal').style.display='none'">Close</button>
                                </div>
                            </div>
                            <div id="cheatSheetContent" class="cs-preview-wrapper well" style="margin:20px; overflow-y:auto; flex:1; font-size:14px; border-radius: 12px; background:var(--bg);"></div>
                        </div>
                    `;
                    document.body.appendChild(modal);

                    el("btnDownloadCS").addEventListener("click", async () => {
                        const opt = {
                            margin:       0.5,
                            filename:     `Cheat_Sheet.pdf`,
                            image:        { type: 'jpeg', quality: 0.98 },
                            html2canvas:  { scale: 2, useCORS: true, letterRendering: true },
                            jsPDF:        { unit: 'in', format: 'a4', orientation: 'portrait' }
                        };
                        const container = document.createElement("div");
                        container.innerHTML = `
                            <style>
                                .cs-pdf-wrapper {
                                    font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
                                    color: #1f2937;
                                    background-color: #ffffff;
                                    line-height: 1.6;
                                }
                                .cs-pdf-wrapper h1 {
                                    text-align: center;
                                    color: #ffffff;
                                    background: linear-gradient(135deg, #4f46e5 0%, #ec4899 100%);
                                    padding: 20px;
                                    border-radius: 12px;
                                    margin-bottom: 30px;
                                    font-size: 28px;
                                    text-transform: uppercase;
                                    letter-spacing: 2px;
                                    -webkit-background-clip: padding-box;
                                }
                                .cs-pdf-wrapper h2 {
                                    color: #4338ca;
                                    border-bottom: 2px solid #e0e7ff;
                                    padding-bottom: 8px;
                                    margin-top: 25px;
                                    font-size: 22px;
                                }
                                .cs-pdf-wrapper h3 {
                                    color: #be185d;
                                    margin-top: 20px;
                                    font-size: 18px;
                                }
                                .cs-pdf-wrapper p, .cs-pdf-wrapper li {
                                    font-size: 14px;
                                    color: #374151;
                                }
                                .cs-pdf-wrapper ul {
                                    padding-left: 20px;
                                }
                                .cs-pdf-wrapper li {
                                    margin-bottom: 6px;
                                }
                                .cs-pdf-wrapper strong {
                                    color: #111827;
                                    background-color: #fef08a;
                                    padding: 2px 4px;
                                    border-radius: 3px;
                                }
                                .cs-pdf-wrapper blockquote {
                                    border-left: 4px solid #3b82f6;
                                    background-color: #eff6ff;
                                    padding: 12px 20px;
                                    margin: 15px 0;
                                    border-radius: 0 8px 8px 0;
                                    color: #1e3a8a;
                                    font-style: italic;
                                }
                                .cs-pdf-wrapper pre {
                                    background-color: #1f2937;
                                    color: #f9fafb;
                                    padding: 15px;
                                    border-radius: 8px;
                                    font-size: 12px;
                                    overflow-x: auto;
                                }
                                .cs-pdf-wrapper code {
                                    background-color: #f3f4f6;
                                    color: #e11d48;
                                    padding: 2px 4px;
                                    border-radius: 4px;
                                    font-family: monospace;
                                    font-size: 13px;
                                }
                                .cs-pdf-wrapper table {
                                    width: 100%;
                                    border-collapse: collapse;
                                    margin: 15px 0;
                                }
                                .cs-pdf-wrapper th, .cs-pdf-wrapper td {
                                    border: 1px solid #e5e7eb;
                                    padding: 10px;
                                    text-align: left;
                                    font-size: 14px;
                                }
                                .cs-pdf-wrapper th {
                                    background-color: #f3f4f6;
                                    color: #4b5563;
                                }
                            </style>
                            <div class="cs-pdf-wrapper">
                                <h1>🚀 Final Review Cheat Sheet</h1>
                                ${renderMarkdown(data.cheat_sheet)}
                            </div>
                        `;
                        const wrapper = document.createElement("div");
                        wrapper.style.position = "absolute";
                        wrapper.style.left = "-9999px";
                        wrapper.style.top = "-9999px";
                        wrapper.appendChild(container);
                        document.body.appendChild(wrapper);

                        const btn = el("btnDownloadCS");
                        const oldText = btn.innerText;
                        btn.innerText = "Generating PDF...";
                        btn.disabled = true;

                        try {
                            await html2pdf().set(opt).from(container).save();
                        } catch (e) {
                            setToast("Error: " + e.message);
                        } finally {
                            document.body.removeChild(wrapper);
                            btn.innerText = oldText;
                            btn.disabled = false;
                        }
                    });
                }
                el("cheatSheetContent").innerHTML = `<h1>Final Review Cheat Sheet</h1>${renderMarkdown(data.cheat_sheet)}`;
                modal.style.display = "flex";
            });

    }
  }

  let explainPopup = null;
  function handleTextSelection(e) {
      if (e.target.closest("#explainPopup") || e.target.closest(".eval-result")) return;

      const selection = window.getSelection();
      const text = selection.toString().trim();

      if (explainPopup) {
          explainPopup.remove();
          explainPopup = null;
      }

      if (text.length > 5 && text.length < 500 && document.querySelector('.tab.active')?.dataset.tab === 'notes') {
          let node = selection.anchorNode;
          let inNotes = false;
          while (node && node !== document.body) {
              if (node.id === "notesMain") { inNotes = true; break; }
              node = node.parentNode;
          }
          if (!inNotes) return;

          const range = selection.getRangeAt(0);
          const rect = range.getBoundingClientRect();

          explainPopup = document.createElement("div");
          explainPopup.id = "explainPopup";
          explainPopup.className = "animate-in";
          explainPopup.style.cssText = `
              position: absolute; top: ${rect.top + window.scrollY - 45}px; left: ${rect.left + window.scrollX + (rect.width/2)}px;
              transform: translateX(-50%); background: #1e1e2f; border: 1px solid rgba(255,255,255,0.2);
              border-radius: 8px; padding: 5px; box-shadow: 0 10px 25px rgba(0,0,0,0.5); z-index: 10000; display: flex; gap: 5px;
          `;

          explainPopup.innerHTML = `
              <button class="btn primary sm" data-action="ELI5">ELI5</button>
              <button class="btn primary sm" data-action="Real-world example">Example</button>
              <button class="btn primary sm" data-action="Summarize">Summarize</button>
          `;

          document.body.appendChild(explainPopup);

          qsa("button", explainPopup).forEach(btn => {
              btn.addEventListener("click", async (ev) => {
                  ev.preventDefault(); ev.stopPropagation();
                  const action = btn.dataset.action;
                  explainPopup.innerHTML = `<div style="padding: 8px 12px; font-size: 13px; color: #9ca3af;">Sage AI is thinking... <span class="spinner sm" style="display:inline-block"></span></div>`;

                  try {
                      const contextNode = range.commonAncestorContainer.parentNode.closest('.qa') || range.commonAncestorContainer.parentNode;
                      const res = await api(BASE + '/api/explain_text', {
                          method: 'POST', headers: { 'Content-Type': 'application/json' },
                          body: JSON.stringify({ text, action, context: contextNode ? contextNode.innerText : text })
                      });
                      explainPopup.style.width = "320px"; explainPopup.style.whiteSpace = "normal";
                      explainPopup.innerHTML = `<div style="padding: 12px; font-size: 13px;"><div style="display:flex; justify-content:space-between; margin-bottom:8px; border-bottom:1px solid rgba(255,255,255,0.1); padding-bottom:8px;"><strong style="color:#3b82f6;">✨ ${action}</strong><button style="background:none;border:none;color:#9ca3af;cursor:pointer;font-size:14px;" onclick="this.closest('#explainPopup').remove()">✕</button></div><div style="max-height: 250px; overflow-y: auto;">${renderMarkdown(res.explanation)}</div></div>`;
                  } catch (err) {
                      explainPopup.innerHTML = `<div style="padding: 12px; color: #ef4444;">Error: ${err.message}</div>`;
                  }
              });
          });
      }
  }

  function initApp() {
      wireTabs();
      wireUpload();
      wireSyllabusPage();
      wirePodcastPage();
      document.addEventListener("mouseup", handleTextSelection);
      document.addEventListener("touchend", handleTextSelection);
      document.body.classList.add("animate-in");
  }

  if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", initApp);
  } else {
      initApp();
  }
})();
