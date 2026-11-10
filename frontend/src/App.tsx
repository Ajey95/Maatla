import { useEffect, useRef, useState } from 'react'
import { AudioLines, BookOpenCheck, Check, ChevronRight, CircleAlert, Clock3, Copy, FileCheck2, Headphones, Link2, MapPin, Mic, MicOff, Phone, PhoneOff, Play, Plus, Search, ShieldCheck, Sparkles, SquarePen, Users, Volume2 } from 'lucide-react'
import { API, request } from './api'
import type { CallSummary, Client, Knowledge, PendingFact, RoomCreated, RoomState, Suggestion, Utterance } from './api'

type Role = 'agent' | 'buyer'
type Status = 'idle' | 'connecting' | 'ready' | 'connected' | 'ended'
interface Source { title: string; url?: string | null; kind: string; updated_at: string; excerpt: string }

const params = new URLSearchParams(location.search)
const initialRoom = params.get('room') || ''
const initialToken = new URLSearchParams(location.hash.slice(1)).get('token') || params.get('token') || ''
const date = (iso?: string | null) => iso ? new Date(iso).toLocaleString('en-IN', { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' }) : '—'
const short = (s: string, n = 130) => s.length > n ? s.slice(0, n) + '…' : s

function App() {
  const [clients, setClients] = useState<Client[]>([])
  const [accessKey, setAccessKey] = useState('')
  const [accessGranted, setAccessGranted] = useState(false)
  const [selectedClient, setSelectedClient] = useState('sample-client')
  const [newClient, setNewClient] = useState('')
  const [room, setRoom] = useState(initialRoom)
  const [token, setToken] = useState(initialToken)
  const [buyerToken, setBuyerToken] = useState('')
  const [role, setRole] = useState<Role>('agent')
  const [state, setState] = useState<RoomState | null>(null)
  const [status, setStatus] = useState<Status>('idle')
  const [muted, setMuted] = useState(false)
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [providerReady, setProviderReady] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [sourceUrl, setSourceUrl] = useState('')
  const [sources, setSources] = useState<Source[]>([])
  const [kbTitle, setKbTitle] = useState('')
  const [kbBody, setKbBody] = useState('')
  const [kbUrl, setKbUrl] = useState('')
  const [kbExpires, setKbExpires] = useState('')
  const [showKnowledgeForm, setShowKnowledgeForm] = useState(false)
  const [demoRunning, setDemoRunning] = useState(false)
  const [approvedReply, setApprovedReply] = useState('')
  const [draftReply, setDraftReply] = useState('')
  const [reviewedTelugu, setReviewedTelugu] = useState('')
  const [reviewedText, setReviewedText] = useState('')
  const [replyPreviewId, setReplyPreviewId] = useState('')
  const [replyPreviewBusy, setReplyPreviewBusy] = useState(false)
  const [replyPreviewError, setReplyPreviewError] = useState('')
  const [coachText, setCoachText] = useState('')
  const [coachTurn, setCoachTurn] = useState('')
  const [coachFinal, setCoachFinal] = useState(false)
  const [coachBusy, setCoachBusy] = useState(false)
  const [buyerConsented, setBuyerConsented] = useState(false)
  const [summary, setSummary] = useState<CallSummary | null>(null)
  const [commitmentText, setCommitmentText] = useState('')
  const [commitmentDue, setCommitmentDue] = useState('')
  const [manualField, setManualField] = useState('area')
  const [manualValue, setManualValue] = useState('')
  const [manualSource, setManualSource] = useState('')
  const [elapsed, setElapsed] = useState(0)
  const wsRef = useRef<WebSocket | null>(null)
  const pcRef = useRef<RTCPeerConnection | null>(null)
  const pendingIceRef = useRef<RTCIceCandidateInit[]>([])
  const streamRef = useRef<MediaStream | null>(null)
  const outboundRef = useRef<MediaStream | null>(null)
  const audioCtxRef = useRef<AudioContext | null>(null)
  const remoteAudioRef = useRef<HTMLAudioElement | null>(null)
  const coachAudioRef = useRef<HTMLAudioElement | null>(null)
  const coachCacheRef = useRef<Record<string, string>>({})
  const transcriptEndRef = useRef<HTMLDivElement | null>(null)
  const draftEditedRef = useRef(false)

  useEffect(() => {
    if (!initialRoom && sessionStorage.getItem('maatla-agent-key')) {
      request<Client[]>('/api/clients').then(list => { setClients(list); setSelectedClient(list[0]?.id || ''); setAccessGranted(true) })
        .catch(() => sessionStorage.removeItem('maatla-agent-key'))
    }
    if (initialRoom && initialToken) {
      request<RoomState>('/api/rooms/' + initialRoom, 'GET', undefined, initialToken)
        .then(s => { setState(s); setRole(s.brief ? 'agent' : 'buyer'); setStatus(s.room.ended_at ? 'ended' : 'idle'); if (s.room.ended_at && s.brief) request<CallSummary>('/api/rooms/' + initialRoom + '/summary', 'GET', undefined, initialToken).then(setSummary).catch(() => {}) })
        .catch(e => setError(String(e)))
      setBuyerToken(localStorage.getItem('maatla-buyer-' + initialRoom) || '')
    }
  }, [])

  useEffect(() => {
    if (!room || !token || status === 'ended') return
    const refresh = setInterval(() => {
      if (wsRef.current?.readyState === WebSocket.OPEN) return
      request<RoomState>('/api/rooms/' + room, 'GET', undefined, token)
        .then(setState).catch(() => {})
    }, 2500)
    return () => clearInterval(refresh)
  }, [room, token, status])

  useEffect(() => {
    if (status !== 'connected') return
    const started = Date.now() - elapsed * 1000
    const timer = setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000)
    return () => clearInterval(timer)
  }, [status])

  useEffect(() => { transcriptEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }) }, [state?.utterances.length])

  useEffect(() => {
    if (status === 'ended' && role === 'agent' && room && token && state?.brief) {
      request<CallSummary>('/api/rooms/' + room + '/summary', 'GET', undefined, token)
        .then(setSummary).catch(() => {})
    }
  }, [status, role, room, token, state?.brief])

  const current = state?.brief?.current || []
  const suggestions = state?.suggestions || []
  const latestBuyerId = [...(state?.utterances || [])].reverse().find(u => u.role === 'buyer')?.id
  const latest = suggestions.find(s => s.utterance_id === latestBuyerId)
  useEffect(() => {
    if (!draftEditedRef.current) setDraftReply(latest?.reply_hindi || '')
  }, [latest?.id])
  useEffect(() => {
    if (role !== 'agent' || !room || !token || !latest?.reply_hindi || !draftReply.trim()) {
      setReviewedTelugu(''); setReviewedText(''); setReplyPreviewId(''); setReplyPreviewBusy(false)
      return
    }
    let cancelled = false
    setReviewedTelugu(''); setReviewedText(''); setReplyPreviewId(''); setReplyPreviewError('')
    setReplyPreviewBusy(true)
    const timer = window.setTimeout(async () => {
      try {
        const result = await request<{ preview_id: string; hinglish: string; telugu: string }>(
          '/api/rooms/' + room + '/reply-preview', 'POST', { text: draftReply.trim() }, token)
        if (!cancelled) {
          setReviewedTelugu(result.telugu); setReviewedText(result.hinglish)
          setReplyPreviewId(result.preview_id)
        }
      } catch (e) {
        if (!cancelled) setReplyPreviewError('Telugu meaning unavailable. Check the translation service and retry by editing the draft.')
      } finally { if (!cancelled) setReplyPreviewBusy(false) }
    }, 650)
    return () => { cancelled = true; window.clearTimeout(timer) }
  }, [draftReply, latest?.id, room, token, role])
  const pending = state?.pending_facts || []
  const meetingLink = room && buyerToken ? location.origin + '/?room=' + encodeURIComponent(room) + '#token=' + encodeURIComponent(buyerToken) : ''

  async function unlockAgent() {
    sessionStorage.setItem('maatla-agent-key', accessKey.trim())
    try {
      const list = await request<Client[]>('/api/clients')
      setClients(list); setSelectedClient(list[0]?.id || ''); setAccessGranted(true); setError('')
    } catch (e) { sessionStorage.removeItem('maatla-agent-key'); setError('Agent access key was not accepted.') }
  }

  async function createClient() {
    if (!newClient.trim()) return
    try {
      const c = await request<Client>('/api/clients', 'POST', { name: newClient.trim() })
      setClients([c, ...clients]); setSelectedClient(c.id); setNewClient('')
    } catch (e) { setError(String(e)) }
  }

  async function createRoom() {
    try {
      const r = await request<RoomCreated>('/api/rooms', 'POST', { client_id: selectedClient })
      localStorage.setItem('maatla-buyer-' + r.id, r.buyer_token)
      history.replaceState(null, '', '/?room=' + encodeURIComponent(r.id) + '#token=' + encodeURIComponent(r.agent_token))
      setRoom(r.id); setToken(r.agent_token); setBuyerToken(r.buyer_token); setRole('agent')
      setState(await request<RoomState>('/api/rooms/' + r.id, 'GET', undefined, r.agent_token))
      setStatus('idle'); setSummary(null); setError('')
    } catch (e) { setError(String(e)) }
  }

  function updateState(updater: (s: RoomState) => RoomState) { setState(s => s ? updater(s) : s) }

  async function handleMessage(event: MessageEvent<string>) {
    let msg: any
    try { msg = JSON.parse(event.data) } catch { return }
    if (msg.type === 'state') { setState(msg.state); return }
    if (msg.type === 'utterance') updateState(s => ({ ...s, utterances: [...s.utterances, msg.utterance as Utterance] }))
    if (msg.type === 'translation') updateState(s => ({ ...s, utterances: s.utterances.map(u => u.id === msg.utterance_id ? { ...u, meaning: msg.meaning } : u) }))
    if (msg.type === 'suggestion') updateState(s => ({ ...s, suggestions: [msg.suggestion as Suggestion, ...(s.suggestions || [])] }))
    if (msg.type === 'coach_preview' && role === 'agent') {
      coachAudioRef.current?.pause()
      setCoachText(msg.text); setCoachTurn(''); setCoachFinal(false)
    }
    if (msg.type === 'coach' && role === 'agent') {
      setCoachText(msg.text); setCoachTurn(msg.utterance_id); setCoachFinal(true)
    }
    if (msg.type === 'pending_fact') updateState(s => ({ ...s, pending_facts: [...(s.pending_facts || []), msg.fact as PendingFact] }))
    if (msg.type === 'sources') setSources(msg.results || [])
    if (msg.type === 'provider_error') setNotice(msg.provider + ': ' + msg.message)
    if (msg.type === 'provider_ready') {
      setProviderReady(true)
      setNotice(role === 'agent'
        ? 'Private transcription is active. Your direct mic is muted; the buyer hears approved Hinglish replies.'
        : 'Gnani Prisma is connected to your microphone.')
    }
    if (msg.type === 'peer_joined' || msg.type === 'peer_present') {
      setStatus('connected')
      if (role === 'agent' && pcRef.current) {
        const offer = await pcRef.current.createOffer()
        await pcRef.current.setLocalDescription(offer)
        wsRef.current?.send(JSON.stringify({ type: 'offer', sdp: offer }))
      }
    }
    if (msg.type === 'peer_left') setStatus('ready')
    if (msg.type === 'call_ended') { leaveCall(); setStatus('ended') }
    if (msg.type === 'offer' && pcRef.current) {
      await pcRef.current.setRemoteDescription(msg.sdp)
      await flushIce()
      const answer = await pcRef.current.createAnswer()
      await pcRef.current.setLocalDescription(answer)
      wsRef.current?.send(JSON.stringify({ type: 'answer', sdp: answer }))
      setStatus('connected')
    }
    if (msg.type === 'answer' && pcRef.current) { await pcRef.current.setRemoteDescription(msg.sdp); await flushIce() }
    if (msg.type === 'ice' && pcRef.current && msg.candidate) {
      if (pcRef.current.remoteDescription) {
        try { await pcRef.current.addIceCandidate(msg.candidate) } catch { /* disconnected peer */ }
      } else pendingIceRef.current.push(msg.candidate)
    }
    if (msg.type === 'approved_reply') {
      setApprovedReply(msg.text)
      if (role === 'buyer' && msg.audio_base64) {
        const audio = new Audio('data:audio/wav;base64,' + msg.audio_base64)
        audio.play().catch(() => setNotice('Tap the play control to hear the approved reply.'))
      }
    }
  }

  async function flushIce() {
    const pending = pendingIceRef.current.splice(0)
    for (const candidate of pending) {
      try { await pcRef.current?.addIceCandidate(candidate) } catch { /* disconnected peer */ }
    }
  }

  async function playCoach(utteranceId: string) {
    if (!utteranceId || !room || !token) return
    setCoachBusy(true)
    try {
      let encoded = coachCacheRef.current[utteranceId]
      if (!encoded) {
        const result = await request<{ text: string; audio_base64: string }>('/api/rooms/' + room + '/coach-voice',
          'POST', { utterance_id: utteranceId }, token)
        encoded = result.audio_base64
        coachCacheRef.current[utteranceId] = encoded
        setCoachText(result.text)
      }
      coachAudioRef.current?.pause()
      const audio = new Audio('data:audio/wav;base64,' + encoded)
      coachAudioRef.current = audio
      await audio.play()
    } catch (e) { setNotice('Private voice coach unavailable. The written cue is still available.'); setError(String(e)) }
    finally { setCoachBusy(false) }
  }

  async function joinCall() {
    if (!room || !token) return
    setStatus('connecting'); setError('')
    try {
      const mic = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true }, video: false })
      streamRef.current = mic
      const outbound = role === 'agent' ? mic.clone() : mic
      outboundRef.current = outbound
      if (role === 'agent') {
        outbound.getAudioTracks().forEach(track => { track.enabled = false })
        setMuted(true)
      }
      const rtcConfig = await request<RTCConfiguration>('/api/rooms/' + room + '/rtc-config', 'GET', undefined, token)
      const pc = new RTCPeerConnection(rtcConfig)
      pcRef.current = pc
      outbound.getTracks().forEach(track => pc.addTrack(track, outbound))
      pc.ontrack = event => { if (remoteAudioRef.current) remoteAudioRef.current.srcObject = event.streams[0] }
      pc.onicecandidate = event => { if (event.candidate && wsRef.current?.readyState === WebSocket.OPEN) wsRef.current.send(JSON.stringify({ type: 'ice', candidate: event.candidate })) }
      const socket = new WebSocket(API.replace(/^http/, 'ws') + '/ws/rooms/' + room)
      wsRef.current = socket
      socket.onmessage = handleMessage
      socket.onclose = () => setStatus(previous => previous === 'ended' ? previous : 'idle')
      socket.onerror = () => setError('Call connection failed. Check the backend and room link.')
      await new Promise<void>((resolve, reject) => { socket.onopen = () => { socket.send(JSON.stringify({ type: 'auth', token })); resolve() }; setTimeout(() => { if (socket.readyState !== WebSocket.OPEN) reject(new Error('Connection timed out')) }, 10000) })
      const ctx = new AudioContext()
      audioCtxRef.current = ctx
      await ctx.audioWorklet.addModule('/pcm-worklet.js')
      const source = ctx.createMediaStreamSource(mic)
      const processor = new AudioWorkletNode(ctx, 'maatla-pcm')
      processor.port.onmessage = e => { if (socket.readyState === WebSocket.OPEN && socket.bufferedAmount < 100_000) socket.send(e.data) }
      source.connect(processor); processor.connect(ctx.destination)
      setStatus('ready')
    } catch (e) { setError(String(e)); setStatus('idle'); leaveCall() }
  }

  function leaveCall() {
    coachAudioRef.current?.pause(); coachAudioRef.current = null
    wsRef.current?.close(); wsRef.current = null
    pcRef.current?.close(); pcRef.current = null
    pendingIceRef.current = []
    outboundRef.current?.getTracks().forEach(track => track.stop()); outboundRef.current = null
    streamRef.current?.getTracks().forEach(track => track.stop()); streamRef.current = null
    audioCtxRef.current?.close(); audioCtxRef.current = null
    setProviderReady(false); setStatus('idle')
  }

  function toggleMute() {
    const next = !muted
    outboundRef.current?.getAudioTracks().forEach(track => { track.enabled = !next })
    setMuted(next)
  }

  async function endCall() {
    if (role !== 'agent') { leaveCall(); setStatus('ended'); return }
    try { setState(await request<RoomState>('/api/rooms/' + room + '/end', 'POST', {}, token)); setSummary(await request<CallSummary>('/api/rooms/' + room + '/summary', 'GET', undefined, token)) }
    catch (e) { setError(String(e)) }
    leaveCall(); setStatus('ended')
  }

  async function copyLink() {
    await navigator.clipboard.writeText(meetingLink)
    setCopied(true); setTimeout(() => setCopied(false), 2000)
  }

  async function runDemo() {
    setDemoRunning(true); setError(''); setNotice('Sample script is synthetic text, not live Gnani transcription.')
    const turns = [
      { role: 'buyer', language: 'hi-IN', text: 'मुझे हैदराबाद के पास 200 गज का प्लॉट चाहिए। मेरा बजट 80 लाख है।' },
      { role: 'agent', language: 'te-IN', text: 'మీకు ఏ ఏరియా బాగుంటుంది? Site visit arrange చేద్దామా?' },
      { role: 'buyer', language: 'hi-IN', text: 'रजिस्ट्री के कागज़ पक्के हैं क्या? और सड़क से प्लॉट तक रास्ता कैसा है?' },
      { role: 'buyer', language: 'hi-IN', text: 'अगर documents ठीक हैं तो शनिवार को site visit कर सकते हैं।' },
    ]
    try {
      for (const turn of turns) {
        await request('/api/rooms/' + room + '/demo-turn', 'POST', turn, token)
        await new Promise(resolve => setTimeout(resolve, 1200))
      }
      const updated = await request<RoomState>('/api/rooms/' + room, 'GET', undefined, token)
      setState(updated)
    } catch (e) { setError(String(e)) }
    setDemoRunning(false)
  }

  async function decideFact(id: string, decision: 'approve' | 'reject') {
    try {
      await request('/api/rooms/' + room + '/facts/' + decision, 'POST', { ids: [id] }, token)
      setState(await request<RoomState>('/api/rooms/' + room, 'GET', undefined, token))
      setNotice(decision === 'approve' ? 'Client fact saved with its original source and date.' : 'Proposed fact dismissed.')
    } catch (e) { setError(String(e)) }
  }

  async function saveManualFact() {
    const source = manualSource || state?.utterances.find(u => u.role === 'buyer')?.id
    if (!source || !manualValue.trim()) return
    try {
      await request('/api/rooms/' + room + '/facts/manual', 'POST',
        { field: manualField, value: manualValue.trim(), source_utterance_id: source }, token)
      setManualValue('')
      setState(await request<RoomState>('/api/rooms/' + room, 'GET', undefined, token))
      setNotice('Reviewed client fact saved with its source statement and date.')
    } catch (e) { setError(String(e)) }
  }

  async function addCommitment() {
    if (!commitmentText.trim()) return
    try {
      await request('/api/rooms/' + room + '/commitments', 'POST',
        { text: commitmentText.trim(), owner: 'agent', due_at: commitmentDue || null }, token)
      setCommitmentText(''); setCommitmentDue('')
      setState(await request<RoomState>('/api/rooms/' + room, 'GET', undefined, token))
    } catch (e) { setError(String(e)) }
  }

  async function completeCommitment(id: string) {
    try {
      await request('/api/rooms/' + room + '/commitments/' + id + '/complete', 'POST', {}, token)
      setState(await request<RoomState>('/api/rooms/' + room, 'GET', undefined, token))
    } catch (e) { setError(String(e)) }
  }

  async function checkSources() {
    if (!searchQuery.trim()) return
    setError('')
    try {
      const data = await request<{ results: Source[] }>('/api/rooms/' + room + '/sources', 'POST',
        { query: searchQuery, fetch_url: sourceUrl.trim() || null }, token)
      setSources(data.results)
      setNotice(data.results.length ? 'Authorized source check complete.' : 'No matching approved sources were found.')
    } catch (e) { setError(String(e)) }
  }

  async function addKnowledge() {
    try {
      await request<Knowledge>('/api/rooms/' + room + '/knowledge', 'POST',
        { title: kbTitle, body: kbBody, url: kbUrl || null, source_kind: 'seller_supplied',
          expires_at: kbExpires ? kbExpires + 'T23:59:59Z' : null }, token)
      setState(await request<RoomState>('/api/rooms/' + room, 'GET', undefined, token))
      setKbTitle(''); setKbBody(''); setKbUrl(''); setKbExpires(''); setShowKnowledgeForm(false)
      setNotice('Seller-supplied document added. It is not marked as official verification.')
    } catch (e) { setError(String(e)) }
  }

  async function speak() {
    if (!latest || !draftReply.trim() || !replyPreviewId || reviewedText !== draftReply.trim() || replyPreviewBusy) return
    try { await request('/api/rooms/' + room + '/speak', 'POST',
      { suggestion_id: latest.id, approved_text: draftReply.trim(), preview_id: replyPreviewId }, token)
      draftEditedRef.current = false }
    catch (e) { setError(String(e)) }
  }

  if (!room || !state) return <div className="app-shell"><header className="brand-header"><span className="wordmark">MAATLA</span><span>Live land-sales call copilot</span></header><main className="lobby">
    <div className="lobby-intro"><span className="eyeline">For a human seller, during a real conversation</span><h1>Understand every buyer. Keep every promise.</h1><p>Telugu-English and Hindi-English live calls, private coaching, sourced land information, and dated follow-up memory.</p><div className="lobby-flow"><span><AudioLines size={18}/> Listen</span><ChevronRight size={16}/><span><Sparkles size={18}/> Understand</span><ChevronRight size={16}/><span><ShieldCheck size={18}/> Respond with evidence</span></div></div>
    <section className="setup-card"><div className="section-icon"><Users size={20}/></div>{!accessGranted ? <><h2>Agent access</h2><p>Enter the private access key configured on this server.</p><input aria-label="Agent access key" type="password" value={accessKey} onChange={e => setAccessKey(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') unlockAgent() }} placeholder="Agent access key"/><button className="primary full" onClick={unlockAgent} disabled={!accessKey.trim()}>Open workspace <ChevronRight size={18}/></button></> : <><h2>Start a buyer conversation</h2><p>Choose an existing client so their earlier context appears before the call.</p><label htmlFor="client-select">Client</label><select id="client-select" value={selectedClient} onChange={e => setSelectedClient(e.target.value)}>{clients.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select><div className="create-client"><input aria-label="New client name" placeholder="Or add a new client" value={newClient} onChange={e => setNewClient(e.target.value)}/><button className="icon-button" onClick={createClient} aria-label="Add client"><Plus size={18}/></button></div><button className="primary full" onClick={createRoom} disabled={!selectedClient}>Create private call <ChevronRight size={18}/></button><p className="setup-note">A private buyer link is created. Live speech needs a Gnani API key; sample script is clearly labelled.</p></>}</section>
    {error && <div className="error-banner"><CircleAlert size={16}/>{error}</div>}
  </main></div>

  if (role === 'buyer') return <div className="app-shell buyer-shell"><header className="brand-header"><span className="wordmark">MAATLA</span><span>Private land conversation</span></header><main className="buyer-view"><div className="buyer-symbol"><Headphones size={44}/></div><h1>Speak with your land advisor</h1><p>Talk naturally in Hindi, English, or a mix. The seller receives private translation and guidance.</p><label className="consent-box"><input type="checkbox" checked={buyerConsented} onChange={e => setBuyerConsented(e.target.checked)}/><span>This call uses AI transcription and translation. I agree to my speech being processed for this conversation.</span></label><div className="call-status"><span className={'status-dot ' + (status === 'connected' ? 'live' : '')}></span>{status === 'connected' ? 'Connected to seller' : status === 'ready' ? 'Waiting for seller' : status === 'connecting' ? 'Connecting…' : status === 'ended' ? 'Call ended' : 'Ready to join'}</div><audio ref={remoteAudioRef} autoPlay playsInline/><div className="buyer-actions">{status === 'idle' ? <button className="primary" onClick={joinCall} disabled={!buyerConsented}><Phone size={18}/> Join call</button> : status === 'ended' ? <p>This conversation has ended.</p> : <><button className="icon-button large" onClick={toggleMute}>{muted ? <MicOff/> : <Mic/>}</button><button className="danger" onClick={endCall}><PhoneOff size={18}/> Leave call</button></>}</div>{approvedReply && <div className="reply-caption"><span>Seller-approved Hinglish reply</span><p lang="hi">{approvedReply}</p></div>}{state.utterances.length > 0 && <div className="buyer-captions"><h2>Conversation captions</h2>{state.utterances.slice(-4).map(u => <div key={u.id} className="buyer-caption"><strong>{u.role === 'agent' ? 'Seller' : 'You'}</strong><p>{u.text}</p>{u.role === 'agent' && u.meaning && <p lang="hi" className="translated">हिन्दी: {u.meaning}</p>}</div>)}</div>}{notice && <div className="notice">{notice}</div>}{error && <div className="error-banner"><CircleAlert size={16}/>{error}</div>}</main></div>

  return <div className="app-shell agent-shell"><header className="brand-header"><span className="wordmark">MAATLA</span><span className="brand-divider"></span><span>Live land-sales call copilot</span><span className="privacy-chip"><ShieldCheck size={16}/> AI speech tools · buyer consents on join</span></header>
    <div className="call-bar"><div className="call-person"><Phone size={21}/><strong>{status === 'connected' ? 'Live call' : 'Call workspace'} with {state.client.name}</strong></div><span className="timer"><Clock3 size={15}/>{String(Math.floor(elapsed / 60)).padStart(2, '0')}:{String(elapsed % 60).padStart(2, '0')}</span><span className="connection"><i className={'status-dot ' + (status === 'connected' ? 'live' : '')}></i>{status === 'connected' ? 'Connected' : status === 'ready' ? 'Waiting for buyer' : status === 'ended' ? 'Call ended' : 'Not connected'}</span><span className="language-pair">తెలుగు + English ↔ हिन्दी + English</span><div className="call-buttons">{status === 'idle' && <><button className="primary" onClick={joinCall}><Phone size={17}/> Join call</button>{state.utterances.length > 0 && <button className="secondary" onClick={endCall}><FileCheck2 size={16}/> Finish & review</button>}</>}{(status === 'ready' || status === 'connected') && <><button className="icon-button" onClick={toggleMute} title={muted ? 'Unmute' : 'Mute'}>{muted ? <MicOff size={18}/> : <Mic size={18}/>}</button><button className="danger" onClick={endCall}><PhoneOff size={17}/> End</button></>}</div></div>
    {meetingLink && <div className="share-strip"><Link2 size={17}/><span>Buyer joining link</span><code>{location.origin + '/?room=' + encodeURIComponent(room) + '#token=••••••'}</code><button onClick={copyLink} className="text-button">{copied ? <Check size={15}/> : <Copy size={15}/>} {copied ? 'Copied' : 'Copy link'}</button></div>}
    {notice && <div className={'notice ' + (providerReady ? 'positive' : '')}>{notice}</div>}{error && <div className="error-banner"><CircleAlert size={16}/>{error}</div>}
    <main className="workspace"><div className="workspace-left"><section className="surface transcript-surface"><div className="surface-heading"><div><h2>Live conversation</h2><p>Original speech and agent-language meaning, separated by speaker.</p></div><span className="private-label"><ShieldCheck size={15}/> Private to seller</span></div><div className="transcript-list">{state.utterances.length ? state.utterances.map(u => <article key={u.id} className={'utterance ' + u.role}><time>{date(u.created_at)}</time><div className="utterance-body"><div className="utterance-meta"><strong>{u.role === 'buyer' ? state.client.name + ' · Buyer' : 'You · Seller'}</strong><span>{u.language || (u.role === 'buyer' ? 'Hindi-English' : 'Telugu-English')}</span>{u.origin === 'synthetic_demo' && <em>Sample text</em>}</div><p className="original" lang={u.role === 'buyer' ? 'hi' : 'te'}>{u.text}</p>{u.meaning && <div className="meaning"><span>{u.role === 'agent' ? 'Hindi for buyer' : 'Meaning in English'}</span>{u.meaning}</div>}</div></article>) : <div className="empty-conversation"><AudioLines size={28}/><h3>Conversation will appear here</h3><p>Join the call and share the buyer link. Each microphone is transcribed separately.</p></div>}<div ref={transcriptEndRef}/></div></section>
    <section className="surface deal-surface"><div className="surface-heading compact"><div><h2>Deal map</h2><p>Only facts the buyer stated and the agent reviewed.</p></div><MapPin size={19}/></div><div className="deal-grid">{['budget', 'plot_size', 'area', 'concern', 'site_visit'].map(field => { const fact = current.find(f => f.field === field); return <div key={field} className="deal-field"><span>{field.replace('_', ' ')}</span><strong>{fact?.value || 'Not confirmed'}</strong></div> })}</div>{pending.length > 0 && <div className="pending-row"><strong>{pending.length} proposed client fact{pending.length > 1 ? 's' : ''} — review each</strong>{pending.map(f => <div className="pending-item" key={f.id}><span>{f.field.replace('_', ' ')}: <b>{f.value}</b><small>Buyer said: {short(state.utterances.find(u => u.id === f.source_utterance_id)?.text || 'Source unavailable', 100)}</small></span><button onClick={() => decideFact(f.id, 'approve')} aria-label={'Approve ' + f.field}><Check size={15}/></button><button onClick={() => decideFact(f.id, 'reject')} aria-label={'Reject ' + f.field}>×</button></div>)}</div>}{state.utterances.some(u => u.role === 'buyer') && <div className="manual-fact"><strong>Add a reviewed detail</strong><div><select aria-label="Fact field" value={manualField} onChange={e => setManualField(e.target.value)}>{['area','budget','plot_size','intended_use','concern','site_visit','decision_maker'].map(field => <option key={field} value={field}>{field.replace('_',' ')}</option>)}</select><input aria-label="Fact value" placeholder="What the buyer stated" value={manualValue} onChange={e => setManualValue(e.target.value)}/></div><select aria-label="Fact source statement" value={manualSource || state.utterances.find(u => u.role === 'buyer')?.id || ''} onChange={e => setManualSource(e.target.value)}>{state.utterances.filter(u => u.role === 'buyer').map(u => <option key={u.id} value={u.id}>{short(u.text, 85)}</option>)}</select><button className="secondary" onClick={saveManualFact} disabled={!manualValue.trim()}>Save reviewed fact</button></div>}</section>
    {summary && <section className="surface summary-surface"><div className="surface-heading compact"><div><h2>Call summary</h2><p>Extractive; source-linked facts require agent approval.</p></div><FileCheck2 size={18}/></div><div className="summary-grid"><div><strong>Buyer requirements</strong>{summary.buyer_requirements.length ? summary.buyer_requirements.map(x => <p key={x.field}>{x.field.replace('_', ' ')}: {x.value}</p>) : <p>None confirmed yet</p>}</div><div><strong>Concerns & next step</strong>{summary.concerns.map((x, i) => <p key={i}>{x.value}</p>)}<p>Site visit: {summary.site_visit || 'Not confirmed'}</p></div><div><strong>Commitments</strong>{summary.commitments.length ? summary.commitments.map(x => <p key={x.id}>{x.text}</p>) : <p>None saved</p>}</div></div></section>}
    <section className="surface demo-surface"><div className="surface-heading compact"><div><h2>Sample script</h2><p>Text-only walkthrough for local verification. It does not call Gnani.</p></div><button className="secondary" disabled={demoRunning || status === 'ended'} onClick={runDemo}><Play size={15}/>{demoRunning ? 'Playing…' : 'Run sample'}</button></div></section></div>
    <aside className="workspace-right"><section className="surface next-move"><div className="surface-heading compact"><div><h2><Sparkles size={20}/> Next move</h2><p>One useful step after the buyer's turn.</p></div></div>{latest ? <><div className="tip-headline">{latest.headline}</div><div className="why"><strong>Why now</strong><p>{latest.why}</p><span className="source-kind">{latest.provider === 'instant_rule' ? 'Immediate cue' : 'Model refinement'} · linked to client turn</span></div>{coachText && <div className="coach-box"><strong>{coachFinal ? 'Telugu coach · complete turn' : 'Quick Telugu gist · updating'}</strong><p lang="te">{coachText}</p><div className="coach-actions">{coachFinal && <button className="secondary" onClick={() => playCoach(coachTurn)} disabled={!coachTurn || coachBusy}><Volume2 size={15}/>{coachBusy ? 'Preparing voice…' : 'Hear Telugu (optional)'}</button>}</div><small>Text appears as Prisma returns speech. Optional voice stays private; the buyer hears approved Hinglish replies.</small></div>}{latest.reply_hindi && <div className="reply-preview"><strong>Hinglish reply to buyer</strong><textarea aria-label="Edit Hinglish reply" lang="hi" value={draftReply} onChange={e => { draftEditedRef.current = true; setDraftReply(e.target.value) }}/><div className="reply-meaning"><strong>Telugu meaning · private review</strong>{replyPreviewBusy ? <p>Translating your latest edit…</p> : reviewedTelugu && reviewedText === draftReply.trim() ? <p lang="te">{reviewedTelugu}</p> : <p>{replyPreviewError || 'Edit the Hinglish reply to refresh its Telugu meaning.'}</p>}</div><span>Check both versions before approving. Only the Hinglish text is spoken to the buyer.</span></div>}{latest.reply_hindi && <button className="primary full" onClick={speak} disabled={status !== "connected" || !draftReply.trim() || !replyPreviewId || reviewedText !== draftReply.trim() || replyPreviewBusy}><Volume2 size={17}/> Approve Hinglish & speak to buyer</button>}<div className="evidence-list">{latest.evidence.map((e, i) => <div key={i}><FileCheck2 size={14}/><span>{e.title || e.kind}{e.updated_at ? ' · ' + date(e.updated_at) : ''}</span></div>)}</div></> : <div className="empty-tip"><Sparkles size={24}/><p>The next question or response appears after a buyer statement.</p></div>}</section>
    <section className="surface continuity"><div className="surface-heading compact"><div><h2><Clock3 size={19}/> Client continuity</h2><p>Follow-up memory, with dates and sources.</p></div></div>{state.brief ? <><div className="continuity-list">{state.brief.current.slice(0, 5).map(f => <div key={f.id}><span>{f.field.replace('_', ' ')}</span><strong>{f.value}</strong><small>{date(f.observed_at)} · {f.source_kind.replaceAll('_', ' ')}</small></div>)}</div>{state.brief.recent_utterances.filter(u => u.room_id !== room).slice(0, 3).length > 0 && <div className="prior-turns"><strong>From earlier calls</strong>{state.brief.recent_utterances.filter(u => u.room_id !== room).slice(0, 3).map(u => <p key={u.id}><small>{date(u.created_at)} · {u.role === 'buyer' ? 'Buyer' : 'Seller'}</small>{short(u.meaning || u.text, 120)}</p>)}</div>}{state.brief.commitments.length > 0 && <div className="commitment-list"><strong>Open commitments</strong>{state.brief.commitments.map(c => <div className="commitment-item" key={c.id}><span>{c.text}{c.due_at && <small>Due {c.due_at}</small>}</span><button onClick={() => completeCommitment(c.id)} aria-label={"Complete commitment " + c.text}><Check size={14}/></button></div>)}</div>}<div className="commitment-entry"><input aria-label="New commitment" placeholder="Promise or follow-up task" value={commitmentText} onChange={e => setCommitmentText(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') addCommitment() }}/><input className="commitment-date" type="date" aria-label="Commitment due date" value={commitmentDue} onInput={e => setCommitmentDue((e.target as HTMLInputElement).value)} onChange={e => setCommitmentDue(e.target.value)}/><button className="icon-button small" onClick={addCommitment} aria-label="Save commitment"><Plus size={16}/></button></div></> : <p>No prior context.</p>}</section>
    <section className="surface source-surface"><div className="surface-heading compact"><div><h2><BookOpenCheck size={19}/> Authorized sources</h2><p>Seller documents first; web only from allowed domains.</p></div><button className="icon-button small" onClick={() => setShowKnowledgeForm(!showKnowledgeForm)} title="Add a seller document"><Plus size={17}/></button></div><div className="source-search"><input aria-label="Search authorized sources" value={searchQuery} onChange={e => setSearchQuery(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') checkSources() }} placeholder="Search plot, document or policy"/><button className="icon-button" onClick={checkSources} aria-label="Search"><Search size={17}/></button></div><input className="source-url" aria-label="Fetch allowed HTTPS URL" value={sourceUrl} onChange={e => setSourceUrl(e.target.value)} placeholder="Fetch allowed HTTPS page (optional)"/><small className="source-help">Live web search requires a Brave key and domain allowlist.</small>{showKnowledgeForm && <div className="knowledge-form"><input aria-label="Knowledge title" placeholder="Document title" value={kbTitle} onChange={e => setKbTitle(e.target.value)}/><textarea aria-label="Knowledge body" placeholder="Approved seller notes or document extract" value={kbBody} onChange={e => setKbBody(e.target.value)}/><input aria-label="Source URL" placeholder="HTTPS source URL (optional)" value={kbUrl} onChange={e => setKbUrl(e.target.value)}/><label className="expiry-label">Use until <input aria-label="Knowledge expiry date" type="date" value={kbExpires} onInput={e => setKbExpires((e.target as HTMLInputElement).value)} onChange={e => setKbExpires(e.target.value)}/></label><button className="secondary" onClick={addKnowledge}><SquarePen size={14}/> Save as seller-supplied</button></div>}{sources.length ? <div className="source-results">{sources.map((s, i) => <article key={i}><span className="source-kind">{s.kind.replaceAll('_', ' ')} · {s.kind.startsWith('allowlisted') ? 'fetched ' : 'updated '}{date(s.updated_at)}</span><strong>{s.url ? <a href={s.url} target="_blank" rel="noreferrer">{s.title}</a> : s.title}</strong><p>{short(s.excerpt, 170)}</p></article>)}</div> : <div className="source-results">{(state.knowledge || []).slice(0, 2).map(k => <article key={k.id}><span className="source-kind">{k.source_kind.replaceAll('_', ' ')} · {date(k.updated_at)}</span><strong>{k.title}</strong><p>{short(k.body, 160)}</p></article>)}</div>}</section></aside></main>
    {status === 'ended' && <div className="ended-banner"><FileCheck2 size={19}/><span>Call ended. Review the deal map and client history before the next call.</span><a href="/">Start another call</a></div>}
    <audio ref={remoteAudioRef} autoPlay playsInline className="remote-audio"/>
  </div>
}

export default App
