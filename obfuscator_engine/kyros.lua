local obf = {}

local _CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
local _CHARS_LEN = #_CHARS

local function rand_name(len)
    local s = {}
    for i = 1, len do
        local idx = math.random(1, _CHARS_LEN)
        s[i] = _CHARS:sub(idx, idx)
    end
    return table.concat(s)
end

local _used = {}

-- BUG FIX: gen_id must never produce a Lua reserved keyword.
-- `or`, `do`, `if`, `in` are 2-char; `and`, `end`, `for`, `nil`, `not`
-- are 3-char; `else`, `goto`, `then`, `true` are 4-char; etc.
-- Any of these as a generated name causes a syntax error in the output,
-- making the obfuscated script fail silently (no prints, no errors).
local _LUA_KEYWORDS = {
    ["and"]=true,  ["break"]=true, ["do"]=true,       ["else"]=true,
    ["elseif"]=true,["end"]=true,  ["false"]=true,    ["for"]=true,
    ["function"]=true,["goto"]=true,["if"]=true,      ["in"]=true,
    ["local"]=true,["nil"]=true,   ["not"]=true,      ["or"]=true,
    ["repeat"]=true,["return"]=true,["then"]=true,    ["true"]=true,
    ["until"]=true,["while"]=true,
}

local function gen_id()
    local max_attempts = 20

    for attempt = 1, max_attempts do
        local len = math.random(2, 3)
        if attempt > 8 then
            len = math.random(3, 4)
        end

        local s = {}
        for i = 1, len do
            local idx = math.random(1, _CHARS_LEN)
            s[i] = _CHARS:sub(idx, idx)
        end
        if math.random(1, 4) == 1 then
            s[math.random(2, len)] = tostring(math.random(0, 9))
        end

        local name = table.concat(s)

        -- Reject Lua keywords and already-used names
        if not _used[name] and not _LUA_KEYWORDS[name] then
            _used[name] = true
            return name
        end
    end

    -- Fallback: prefix with underscore guarantees it is never a keyword
    local name = "_v" .. tostring(#_used)
    _used[name] = true
    return name
end

-- ASCII85 (Base85) encoder
local function a85_encode(data)
    local out = {}
    local n = #data
    local i = 1
    while i <= n do
        local remaining = n - i + 1
        if remaining >= 4 then
            local b1, b2, b3, b4 = data:byte(i, i + 3)
            local val = b1 * 16777216 + b2 * 65536 + b3 * 256 + b4
            if val == 0 then
                out[#out + 1] = "z"
            else
                local c5 = val % 85; val = (val - c5) / 85
                local c4 = val % 85; val = (val - c4) / 85
                local c3 = val % 85; val = (val - c3) / 85
                local c2 = val % 85; val = (val - c2) / 85
                local c1 = val % 85
                out[#out + 1] = string.char(33 + c1, 33 + c2, 33 + c3, 33 + c4, 33 + c5)
            end
            i = i + 4
        else
            local b1 = data:byte(i) or 0
            local b2 = (remaining >= 2) and data:byte(i + 1) or 0
            local b3 = (remaining >= 3) and data:byte(i + 2) or 0
            local val = b1 * 16777216 + b2 * 65536 + b3 * 256
            local chars = {}
            for _ = 1, 5 do
                local r = val % 85
                chars[#chars + 1] = r
                val = (val - r) / 85
            end
            local needed = remaining + 1
            for j = 5, 5 - needed + 1, -1 do
                out[#out + 1] = string.char(33 + chars[j])
            end
            i = n + 1
        end
    end
    return table.concat(out)
end

-- Standard base64 for KRS_NOVIRTUALIZE string encoding
local STD_B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
local function std_b64_encode(data)
    local out = {}
    local pad = #data % 3
    for i = 1, #data - pad, 3 do
        local b1, b2, b3 = data:byte(i, i+2)
        local n = b1 * 65536 + b2 * 256 + b3
        out[#out+1] = STD_B64:sub((n >> 18) + 1, (n >> 18) + 1)
        out[#out+1] = STD_B64:sub(((n >> 12) & 63) + 1, ((n >> 12) & 63) + 1)
        out[#out+1] = STD_B64:sub(((n >> 6) & 63) + 1, ((n >> 6) & 63) + 1)
        out[#out+1] = STD_B64:sub((n & 63) + 1, (n & 63) + 1)
    end
    if pad == 1 then
        local b1 = data:byte(#data)
        local n = b1 * 65536
        out[#out+1] = STD_B64:sub((n >> 18) + 1, (n >> 18) + 1)
        out[#out+1] = STD_B64:sub(((n >> 12) & 63) + 1, ((n >> 12) & 63) + 1)
        out[#out+1] = "=="
    elseif pad == 2 then
        local b1, b2 = data:byte(#data-1, #data)
        local n = b1 * 65536 + b2 * 256
        out[#out+1] = STD_B64:sub((n >> 18) + 1, (n >> 18) + 1)
        out[#out+1] = STD_B64:sub(((n >> 12) & 63) + 1, ((n >> 12) & 63) + 1)
        out[#out+1] = STD_B64:sub(((n >> 6) & 63) + 1, ((n >> 6) & 63) + 1)
        out[#out+1] = "="
    end
    return table.concat(out)
end

local function xor_cipher(data, key)
    local out = {}
    local h = key
    for i = 1, #data do
        local j = h % 256
        local xb = data:byte(i) ~ j
        out[i] = string.char(xb)
        h = (h * 0x83 + xb + 1) % 4294967296
    end
    return table.concat(out)
end

local function lz_compress(data)
    local out = {}
    local i = 1
    local n = #data
    local WINDOW = 2048
    local MAX_LEN = 255
    local head = {}
    local prev = {}
    local function write_varint(val)
        repeat
            local b = val & 0x7F
            val = val >> 7
            if val > 0 then b = b | 0x80 end
            out[#out+1] = string.char(b)
        until val == 0
    end
    local function hash3(pos)
        if pos + 2 > n then return nil end
        local b1, b2, b3 = data:byte(pos, pos + 2)
        return b1 * 65536 + b2 * 256 + b3
    end
    local function insert_hash(pos)
        local k = hash3(pos)
        if not k then return end
        prev[pos] = head[k]
        head[k] = pos
    end
    while i <= n do
        local items = {}
        for bit = 0, 7 do
            if i > n then break end
            local best_off, best_len = 0, 0
            local k = hash3(i)
            if k then
                local p = head[k]
                local search_min = i - WINDOW
                local checked = 0
                while p and p >= search_min and checked < 64 do
                    checked = checked + 1
                    local len = 0
                    while i + len <= n and len < MAX_LEN
                          and data:byte(p + len) == data:byte(i + len) do
                        len = len + 1
                    end
                    if len > best_len then
                        best_len = len
                        best_off = i - p
                        if best_len >= MAX_LEN then break end
                    end
                    p = prev[p]
                end
            end
            if best_len >= 3 then
                items[#items+1] = { ref=true, off=best_off, len=best_len }
                for _ = 1, best_len do
                    insert_hash(i)
                    i = i + 1
                end
            else
                items[#items+1] = { ref=false, byte=data:sub(i, i) }
                insert_hash(i)
                i = i + 1
            end
        end
        if #items == 0 then break end
        local flag = 0
        for k, item in ipairs(items) do
            if item.ref then flag = flag | (1 << (k - 1)) end
        end
        out[#out+1] = string.char(flag)
        for _, item in ipairs(items) do
            if item.ref then
                write_varint(item.off)
                write_varint(item.len)
            else
                out[#out+1] = item.byte
            end
        end
    end
    return table.concat(out)
end

local function write_varint(val)
    local out = {}
    repeat
        local byte = val & 0x7F
        val = val >> 7
        if val > 0 then byte = byte | 0x80 end
        out[#out+1] = string.char(byte)
    until val == 0
    return table.concat(out)
end

local function write_signed_varint(val)
    local zz = val >= 0 and val * 2 or ((-val) * 2 - 1)
    return write_varint(zz)
end

local OPCODES = {
    [0]  = { name="LOADK",    fmt="rr"   },
    [1]  = { name="LOADBOOL", fmt="rr"   },
    [2]  = { name="LOADNIL",  fmt="r"    },
    [3]  = { name="MOVE",     fmt="rr"   },
    [4]  = { name="GETENV",   fmt="rr"   },
    [5]  = { name="SETENV",   fmt="rr"   },
    [6]  = { name="GETTABLE", fmt="rrr"  },
    [7]  = { name="SETTABLE", fmt="rrr"  },
    [8]  = { name="NEWTABLE", fmt="r"    },
    [9]  = { name="ADD",      fmt="rrr"  },
    [10] = { name="SUB",      fmt="rrr"  },
    [11] = { name="MUL",      fmt="rrr"  },
    [12] = { name="DIV",      fmt="rrr"  },
    [13] = { name="MOD",      fmt="rrr"  },
    [14] = { name="POW",      fmt="rrr"  },
    [15] = { name="NEG",      fmt="rr"   },
    [16] = { name="LEN",      fmt="rr"   },
    [17] = { name="BAND",     fmt="rrr"  },
    [18] = { name="BOR",      fmt="rrr"  },
    [19] = { name="BXOR",     fmt="rrr"  },
    [20] = { name="BNOT",     fmt="rr"   },
    [21] = { name="BSHL",     fmt="rrr"  },
    [22] = { name="BSHR",     fmt="rrr"  },
    [23] = { name="CONCAT",   fmt="rrr"  },
    [24] = { name="EQ",       fmt="rrr"  },
    [25] = { name="NEQ",      fmt="rrr"  },
    [26] = { name="LT",       fmt="rrr"  },
    [27] = { name="LE",       fmt="rrr"  },
    [28] = { name="GT",       fmt="rrr"  },
    [29] = { name="GE",       fmt="rrr"  },
    [30] = { name="NOT",      fmt="rr"   },
    [31] = { name="PRINT",    fmt="rr"   },
    [32] = { name="JMP",      fmt="s"    },
    [33] = { name="JMPIF",    fmt="rs"   },
    [34] = { name="CLOSURE",  fmt="rr"   },
    [35] = { name="CALL",     fmt="rrr"  },
    [36] = { name="RETURN",   fmt="rr"   },
    [37] = { name="TAILCALL", fmt="rr"   },
    [38] = { name="VARARG",   fmt="rr"   },
    [39] = { name="FORPREP",  fmt="rrsr" },
    [40] = { name="FORSTEP",  fmt="rrsr" },
    [41] = { name="HALT",     fmt=""     },
    [42] = { name="GETUPVAL", fmt="rr"   },
    [43] = { name="SETUPVAL", fmt="rr"   },
    [44] = { name="S_LOADK_MOVE", fmt="rrr"  },
    [45] = { name="S_MOVE2",      fmt="rrr"  },
    [46] = { name="S_LOADNIL2",   fmt="rr"   },
    [47] = { name="S_NOT_NOT",    fmt="rr"   },
    [48] = { name="S_ARITH_K",    fmt="rrrr" },
    [49] = { name="S_GETTABLE_K", fmt="rrr"  },
    [50] = { name="S_SETTABLE_K", fmt="rrr"  },
    [51] = { name="S_LOADK2",     fmt="rrrr" },
    [52] = { name="S_MOVE_LOADK", fmt="rrrr" },
}
local NUM_OPCODES = 53


----------------------------------------------------------------------------
-- LEXER
----------------------------------------------------------------------------
local Lexer = {}
Lexer.__index = Lexer

local KW = {
    ["local"]=true, ["if"]=true,       ["then"]=true,     ["else"]=true,
    ["elseif"]=true,["end"]=true,      ["for"]=true,      ["do"]=true,
    ["while"]=true,                     ["and"]=true,      ["or"]=true,
    ["not"]=true,   ["true"]=true,     ["false"]=true,    ["nil"]=true,
    ["return"]=true,["function"]=true, ["repeat"]=true,   ["until"]=true,
    ["break"]=true, ["in"]=true,
}

local MULTI_OPS_3 = { ["..."] = true }
local MULTI_OPS_2 = {
    ["=="]=true, ["~="]=true, ["<="]=true, [">="]=true,
    [".."]=true, [">>"]=true, ["<<"]=true,
}

function Lexer.new(source)
    local self = setmetatable({}, Lexer)
    self._src    = source
    self._pos    = 1
    self._tokens = {}
    self._ti     = 1
    self:_scan_all()
    return self
end

local function match_long_open(src, pos)
    if src:sub(pos, pos) ~= '[' then return nil end
    local i = pos + 1
    local eq = 0
    while src:sub(i, i) == '=' do
        eq = eq + 1
        i = i + 1
    end
    if src:sub(i, i) ~= '[' then return nil end
    return eq, i + 1
end

local function match_long_close(src, pos, eq)
    local close = ']' .. string.rep('=', eq) .. ']'
    local clen = #close
    while pos <= #src do
        if src:sub(pos, pos + clen - 1) == close then
            return pos + clen
        end
        pos = pos + 1
    end
    return #src + 1
end

function Lexer:_skip_ws()
    local src = self._src
    local pos = self._pos
    while pos <= #src do
        local c = src:sub(pos, pos)
        if c == ' ' or c == '\t' or c == '\r' or c == '\n' then
            pos = pos + 1
        elseif src:sub(pos, pos+1) == '--' then
            local eq, after = match_long_open(src, pos + 2)
            if eq ~= nil then
                pos = match_long_close(src, after, eq)
            else
                pos = pos + 2
                while pos <= #src and src:sub(pos, pos) ~= '\n' do
                    pos = pos + 1
                end
            end
        else
            break
        end
    end
    self._pos = pos
end

function Lexer:_read_short_string(q)
    self._pos = self._pos + 1
    local src = self._src
    local s   = {}
    while self._pos <= #src and src:sub(self._pos, self._pos) ~= q do
        if src:sub(self._pos, self._pos) == '\\' then
            self._pos = self._pos + 1
            local e = src:sub(self._pos, self._pos)
            if     e == 'n'  then s[#s+1] = '\n'
            elseif e == 't'  then s[#s+1] = '\t'
            elseif e == 'r'  then s[#s+1] = '\r'
            elseif e == '0'  then s[#s+1] = '\0'
            else                  s[#s+1] = e
            end
        else
            s[#s+1] = src:sub(self._pos, self._pos)
        end
        self._pos = self._pos + 1
    end
    self._pos = self._pos + 1
    return table.concat(s)
end

function Lexer:_read_long_string()
    local src = self._src
    local eq, after = match_long_open(src, self._pos)
    if eq == nil then
        error("Invalid long string opening at position " .. self._pos)
    end
    local close_len = 2 + eq
    local close_pos = match_long_close(src, after, eq)
    local start = after
    if src:sub(start, start) == '\n' then
        start = start + 1
    elseif src:sub(start, start + 1) == '\r\n' then
        start = start + 2
    end
    local content_end = close_pos - close_len - 1
    local content = (content_end >= start) and src:sub(start, content_end) or ""
    self._pos = close_pos
    return content
end

function Lexer:_scan_all()
    local src = self._src
    while true do
        self:_skip_ws()
        if self._pos > #src then break end

        local c  = src:sub(self._pos, self._pos)
        local tok
        local tok_start = self._pos

        if c == '[' and match_long_open(src, self._pos) ~= nil then
            tok = { type="string", val=self:_read_long_string() }
        elseif c == '"' or c == "'" then
            tok = { type="string", val=self:_read_short_string(c) }
        elseif c:match('%d') or (c == '.' and src:sub(self._pos+1, self._pos+1):match('%d')) then
            local st = self._pos
            if src:sub(self._pos, self._pos+1):lower() == '0x' then
                self._pos = self._pos + 2
                while self._pos <= #src and src:sub(self._pos, self._pos):match('[%x_]') do
                    self._pos = self._pos + 1
                end
            else
                while self._pos <= #src do
                    local ch = src:sub(self._pos, self._pos)
                    if ch:match('[%d%.eEpP_]') then
                        if ch:match('[eEpP]') then
                            local nc = src:sub(self._pos+1, self._pos+1)
                            if nc == '+' or nc == '-' then self._pos = self._pos + 1 end
                        end
                        self._pos = self._pos + 1
                    else
                        break
                    end
                end
            end
            local raw = src:sub(st, self._pos-1):gsub('_', '')
            tok = { type="number", val=tonumber(raw) }
        elseif c:match('[%a_]') then
            local st = self._pos
            while self._pos <= #src and src:sub(self._pos, self._pos):match('[%w_]') do
                self._pos = self._pos + 1
            end
            local word = src:sub(st, self._pos-1)
            tok = KW[word] and { type="kw", val=word } or { type="id", val=word }
        else
            local three = src:sub(self._pos, self._pos+2)
            local two   = src:sub(self._pos, self._pos+1)
            if MULTI_OPS_3[three] then
                self._pos = self._pos + 3
                tok = { type="op", val=three }
            elseif MULTI_OPS_2[two] then
                self._pos = self._pos + 2
                tok = { type="op", val=two }
            else
                self._pos = self._pos + 1
                tok = { type="op", val=c }
            end
        end

        tok.start = tok_start
        tok.finish = self._pos
        self._tokens[#self._tokens+1] = tok
    end
end

function Lexer:peek()
    return self._tokens[self._ti]
end

function Lexer:peek2()
    return self._tokens[self._ti + 1]
end

function Lexer:next()
    local t = self._tokens[self._ti]
    self._ti = self._ti + 1
    return t
end

function Lexer:expect(typ, val)
    local t = self:next()
    if not t then
        error("EOF: expected " .. typ .. (val and (" '"..val.."'") or ""))
    end
    if t.type ~= typ then
        error(("Expected %s got %s('%s') at token %d"):format(
              typ, t.type, tostring(t.val), self._ti))
    end
    if val and t.val ~= val then
        error(("Expected '%s' got '%s'"):format(val, tostring(t.val)))
    end
    return t
end

function Lexer:check(typ, val)
    local t = self._tokens[self._ti]
    if not t then return false end
    if t.type ~= typ then return false end
    if val and t.val ~= val then return false end
    return true
end

function Lexer:match(typ, val)
    if self:check(typ, val) then self:next(); return true end
    return false
end

function Lexer:save()
    return self._ti
end

function Lexer:restore(pos)
    self._ti = pos
end

local vB64D = rand_name(3)

----------------------------------------------------------------------------
-- KRS_NOVIRTUALIZE string encoding
----------------------------------------------------------------------------
local NOVIRTUALIZE_GLOBALS = {
    ["print"]=true, ["warn"]=true, ["error"]=true, ["pairs"]=true,
    ["ipairs"]=true, ["next"]=true, ["select"]=true, ["type"]=true,
    ["typeof"]=true, ["tostring"]=true, ["tonumber"]=true, ["pcall"]=true,
    ["xpcall"]=true, ["rawget"]=true, ["rawset"]=true, ["rawequal"]=true,
    ["rawlen"]=true, ["setmetatable"]=true, ["getmetatable"]=true,
    ["require"]=true, ["loadstring"]=true, ["load"]=true, ["unpack"]=true,
    ["table"]=true, ["string"]=true, ["math"]=true, ["os"]=true,
    ["coroutine"]=true, ["io"]=true, ["bit32"]=true, ["utf8"]=true,
    ["game"]=true, ["workspace"]=true, ["script"]=true, ["wait"]=true,
    ["task"]=true, ["Instance"]=true, ["Vector3"]=true, ["Vector2"]=true,
    ["CFrame"]=true, ["Color3"]=true, ["UDim"]=true, ["UDim2"]=true,
    ["BrickColor"]=true, ["Enum"]=true, ["tick"]=true, ["time"]=true,
    ["collectgarbage"]=true, ["assert"]=true, ["getfenv"]=true,
    ["setfenv"]=true, ["dofile"]=true,
}

local function encode_NOVIRTUALIZE_strings(body_src, decoder_name)
    local lex = Lexer.new(body_src)
    local parts = {}
    local last_pos = 1
    local had = false

    -- BUG FIX: the original overwrote the parameter with the module-level vB64D.
    -- We now honour the passed-in decoder_name if provided, else fall back to vB64D.
    decoder_name = decoder_name or vB64D

    local fenv_var = gen_id()

    while true do
        local t = lex:next()
        if not t then break end

        if t.type == "string" then
            if t.start > last_pos then
                parts[#parts+1] = body_src:sub(last_pos, t.start - 1)
            end
            local encoded = std_b64_encode(t.val)
            local lit = string.format("%q", encoded)
            parts[#parts+1] = decoder_name .. "(" .. lit .. ")"
            last_pos = t.finish
            had = true

        elseif t.type == "id" and NOVIRTUALIZE_GLOBALS[t.val] then
            local preceding = body_src:sub(math.max(1, t.start - 10), t.start - 1)
            local is_field_access = preceding:match("%.[%s]*$") ~= nil

            if not is_field_access then
                if t.start > last_pos then
                    parts[#parts+1] = body_src:sub(last_pos, t.start - 1)
                end
                local encoded = std_b64_encode(t.val)
                local lit = string.format("%q", encoded)
                parts[#parts+1] = fenv_var .. "[" .. decoder_name .. "(" .. lit .. ")]"
                last_pos = t.finish
                had = true
            end
        end
    end

    if last_pos <= #body_src then
        parts[#parts+1] = body_src:sub(last_pos)
    end

    if not had then
        return body_src, false
    end

    local rewritten = table.concat(parts)
    local inject = ("local %s=getfenv();"):format(fenv_var)
    rewritten = rewritten:gsub("^(function%s*%(.-%)%s*)", function(head)
        return head .. inject
    end, 1)

    return rewritten, true
end

----------------------------------------------------------------------------
-- SOURCE PREPROCESS
----------------------------------------------------------------------------
local function preprocess_source(source)
    local lex = Lexer.new(source)
    local toks = {}
    while true do
        local t = lex:next()
        if not t then break end
        toks[#toks + 1] = t
    end
    if #toks == 0 then return source end

    local function is_colon(i)
        return toks[i] and toks[i].type == "op" and toks[i].val == ":"
    end
    local function is_dot(i)
        return toks[i] and toks[i].type == "op" and toks[i].val == "."
    end
    local function is_id(i)
        return toks[i] and toks[i].type == "id"
    end
    local function is_lparen(i)
        return toks[i] and toks[i].type == "op" and toks[i].val == "("
    end

    local function is_function_def_at(idx)
        for m = math.max(1, idx - 10), idx - 1 do
            if toks[m].type == "kw" and toks[m].val == "function" then
                local ok = true
                for n = m + 1, idx - 1 do
                    local t = toks[n]
                    if t.type == "op" and (t.val == "=" or t.val == "(") then
                        ok = false
                        break
                    end
                end
                if ok then return true end
            end
        end
        return false
    end

    local function find_prefix_start(colon_i)
        local i = colon_i - 1
        if i < 1 then return 1 end
        while i >= 1 do
            local t = toks[i]
            if t.type == "op" and t.val == ")" then
                local depth = 1
                i = i - 1
                while i >= 1 and depth > 0 do
                    local u = toks[i]
                    if u.type == "op" and u.val == ")" then depth = depth + 1
                    elseif u.type == "op" and u.val == "(" then depth = depth - 1
                    end
                    i = i - 1
                end
            elseif t.type == "op" and t.val == "]" then
                local depth = 1
                i = i - 1
                while i >= 1 and depth > 0 do
                    local u = toks[i]
                    if u.type == "op" and u.val == "]" then depth = depth + 1
                    elseif u.type == "op" and u.val == "[" then depth = depth - 1
                    end
                    i = i - 1
                end
            elseif t.type == "id" then
                i = i - 1
                if i >= 1 and toks[i].type == "op" and (toks[i].val == "." or toks[i].val == ":") then
                    i = i - 1
                else
                    break
                end
            else
                break
            end
        end
        return i + 1
    end

    local function slice_src(a, b)
        if a > b then return "" end
        return source:sub(toks[a].start, toks[b].finish - 1)
    end

    local function gap(from_pos, to_pos)
        if to_pos > from_pos then
            return source:sub(from_pos, to_pos - 1)
        end
        return ""
    end

    -- Pass 1: colon method calls
    local parts = {}
    local last_pos = 1
    local i = 1
    while i <= #toks do
        if is_colon(i) and is_id(i + 1) and is_lparen(i + 2)
           and not is_function_def_at(i) then
            local colon_i = i
            local name_i = i + 1
            local paren_i = i + 2
            local pref_a = find_prefix_start(colon_i)
            local pref_b = colon_i - 1
            local prefix_text = slice_src(pref_a, pref_b)
            local method_name = toks[name_i].val

            parts[#parts + 1] = gap(last_pos, toks[colon_i].start)
            parts[#parts + 1] = "."
            parts[#parts + 1] = method_name
            parts[#parts + 1] = "("
            parts[#parts + 1] = prefix_text
            local after = paren_i + 1
            if after <= #toks and not (toks[after].type == "op" and toks[after].val == ")") then
                parts[#parts + 1] = ","
            end
            last_pos = toks[paren_i].finish
            i = paren_i + 1
        else
            parts[#parts + 1] = gap(last_pos, toks[i].start)
            parts[#parts + 1] = source:sub(toks[i].start, toks[i].finish - 1)
            last_pos = toks[i].finish
            i = i + 1
        end
    end
    parts[#parts + 1] = source:sub(last_pos)
    local after_colon = table.concat(parts)

    -- Pass 2: dot field access -> brackets
    local lex2 = Lexer.new(after_colon)
    local toks2 = {}
    while true do
        local t = lex2:next()
        if not t then break end
        toks2[#toks2 + 1] = t
    end

    local function is_func_def_dot(idx)
        for m = math.max(1, idx - 10), idx - 1 do
            if toks2[m].type == "kw" and toks2[m].val == "function" then
                local ok = true
                for n = m + 1, idx - 1 do
                    local t = toks2[n]
                    if t.type == "op" and (t.val == "=" or t.val == "(") then
                        ok = false
                        break
                    end
                end
                if ok then return true end
            end
        end
        return false
    end

    local parts2 = {}
    local last2 = 1
    local j = 1
    while j <= #toks2 do
        if toks2[j].type == "op" and toks2[j].val == "."
           and toks2[j + 1] and toks2[j + 1].type == "id"
           and not is_func_def_dot(j) then
            if toks2[j].start > last2 then
                parts2[#parts2 + 1] = after_colon:sub(last2, toks2[j].start - 1)
            end
            parts2[#parts2 + 1] = "['"
            parts2[#parts2 + 1] = toks2[j + 1].val
            parts2[#parts2 + 1] = "']"
            last2 = toks2[j + 1].finish
            j = j + 2
        else
            if toks2[j].start > last2 then
                parts2[#parts2 + 1] = after_colon:sub(last2, toks2[j].start - 1)
            end
            parts2[#parts2 + 1] = after_colon:sub(toks2[j].start, toks2[j].finish - 1)
            last2 = toks2[j].finish
            j = j + 1
        end
    end
    parts2[#parts2 + 1] = after_colon:sub(last2)
    return table.concat(parts2)
end

----------------------------------------------------------------------------
-- COMPILER
----------------------------------------------------------------------------
local function compile(source)
    source = preprocess_source(source)
    local lex = Lexer.new(source)

    local constants   = {}
    local const_map   = {}
    local const_count = 0

    local function add_const(val)
        local key
        if type(val) == "table" and val.__krs_encnum then
            key = "encnum:" .. tostring(val.v)
        else
            key = type(val) .. ":" .. tostring(val)
        end
        if const_map[key] then return const_map[key] end
        local idx = const_count
        const_count = const_count + 1
        constants[idx] = val
        const_map[key] = idx
        return idx
    end

    local protos     = {}
    -- BUG FIX 1 & 2: declare suppress_protos and proto_count BEFORE add_proto
    -- so that add_proto's closure captures the correct locals, not globals.
    local suppress_protos = false
    local proto_count     = 0

    local function add_proto(proto)
        if suppress_protos then return 0 end
        local idx = proto_count
        proto_count = proto_count + 1
        protos[idx] = proto
        return idx
    end

    local NOVIRTUALIZEs = {}

    -- BUG FIX 3: removed dead function emit_const_to_reg entirely.
    -- It was never called and referenced emit before emit was declared.

    local ctx_stack = {}

    local function cur() return ctx_stack[#ctx_stack] end

    local function push_ctx(params, is_vararg)
        local c = {
            instructions  = {},
            locals        = {},
            upvalues      = {},
            upval_map     = {},
            reg_top       = params or 0,
            params        = params or 0,
            is_vararg     = is_vararg or false,
            break_patches = {},
        }
        ctx_stack[#ctx_stack+1] = c
        return c
    end

    local function pop_ctx()
        local c = ctx_stack[#ctx_stack]
        ctx_stack[#ctx_stack] = nil
        return c
    end

    local function alloc_reg()
        local c = cur()
        local r = c.reg_top
        c.reg_top = c.reg_top + 1
        return r
    end

    local function emit(op, a, b, c_arg, d)
        local ctx = cur()
        local ins = ctx.instructions
        ins[#ins+1] = {op=op, a=a or 0, b=b or 0, c=c_arg or 0, d=d or 0}
        return #ins
    end

    local function patch(idx, field, val)
        cur().instructions[idx][field] = val
    end

    local function Rrk(r) return r * 2 end
    local function Krk(k) return k * 2 + 1 end

    local function to_reg(rk, hint)
        if rk % 2 == 0 then return rk // 2 end
        local dst = hint ~= nil and hint or alloc_reg()
        emit(0, dst, (rk-1) // 2)
        return dst
    end

    local function resolve_name(name)
        local ctx = cur()
        if ctx.locals[name] ~= nil then
            return ctx.locals[name], "local"
        end
        if ctx.upval_map[name] ~= nil then
            return ctx.upval_map[name], "upval"
        end

        local current_level = #ctx_stack

        local owner_level = nil
        for i = current_level - 1, 1, -1 do
            local c = ctx_stack[i]
            if c.locals[name] ~= nil or c.upval_map[name] ~= nil then
                owner_level = i
                break
            end
        end
        if not owner_level then
            return nil, "global"
        end

        local function add_upval(level, desc)
            local c = ctx_stack[level]
            if c.upval_map[name] ~= nil then
                return c.upval_map[name]
            end
            local uvidx = #c.upvalues + 1
            c.upvalues[uvidx] = desc
            c.upval_map[name] = uvidx
            return uvidx
        end

        local owner = ctx_stack[owner_level]
        local prev_uv
        if owner.locals[name] ~= nil then
            prev_uv = add_upval(owner_level + 1, {
                instack = true,
                idx = owner.locals[name],
            })
        else
            prev_uv = add_upval(owner_level + 1, {
                instack = false,
                idx = owner.upval_map[name],
            })
        end

        for level = owner_level + 2, current_level do
            prev_uv = add_upval(level, {
                instack = false,
                idx = prev_uv,
            })
        end

        return prev_uv, "upval"
    end

    local function lookup_local(name)
        return resolve_name(name)
    end

    local parse_expr, parse_block, parse_stat

    local BINOP_PREC = {
        ["or"]=1, ["and"]=2,
        ["=="]=3, ["~="]=3, ["<"]=3, ["<="]=3, [">"]=3, [">="]=3,
        [".."]=4,
        ["+"]=5, ["-"]=5,
        ["*"]=6, ["/"]=6, ["%"]=6,
        ["&"]=3, ["|"]=3, ["~"]=3, ["<<"]=3, [">>"]=3,
        ["^"]=8,
    }
    local BINOP_OP = {
        ["+"]=9,  ["-"]=10, ["*"]=11, ["/"]=12, ["%"]=13, ["^"]=14,
        [".."]=23,
        ["=="]=24, ["~="]=25, ["<"]=26, ["<="]=27, [">"]=28, [">="]=29,
        ["&"]=17,  ["|"]=18, ["~"]=19, ["<<"]=21, [">>"]=22,
    }

    local function emit_args(base_reg)
        local count = 0
        if not lex:check("op", ")") then
            repeat
                local dst = base_reg + count
                if cur().reg_top <= dst then cur().reg_top = dst + 1 end
                local rk  = parse_expr(0)
                local src  = to_reg(rk, dst)
                if src ~= dst then emit(3, dst, src) end
                cur().reg_top = dst + 1
                count = count + 1
            until not lex:match("op", ",")
        end
        lex:expect("op", ")")
        return count
    end

    local function parse_table_constructor()
        lex:expect("op", "{")
        local tbl_r = alloc_reg()
        emit(8, tbl_r)

        local arr_idx = 0
        while not lex:check("op", "}") do
            local save = cur().reg_top
            local key_r, val_r

            if lex:check("op", "[") then
                lex:next()
                local krk = parse_expr(0); cur().reg_top = save
                key_r = alloc_reg()
                local ks = to_reg(krk, key_r)
                if ks ~= key_r then emit(3, key_r, ks) end
                lex:expect("op", "]"); lex:expect("op", "=")
                local vrk = parse_expr(0); cur().reg_top = save
                val_r = alloc_reg()
                local vs = to_reg(vrk, val_r)
                if vs ~= val_r then emit(3, val_r, vs) end
                emit(7, tbl_r, key_r, val_r)

            elseif lex:check("id") and lex:peek2()
               and lex:peek2().type == "op" and lex:peek2().val == "=" then
                local field = lex:next().val; lex:next()
                key_r = alloc_reg()
                emit(0, key_r, add_const(field))
                local key_r_saved = key_r
                local vrk = parse_expr(0)
                val_r = alloc_reg()
                local vs = to_reg(vrk, val_r)
                if vs ~= val_r then emit(3, val_r, vs) end
                emit(7, tbl_r, key_r_saved, val_r)
                cur().reg_top = save + 1
            else
                arr_idx = arr_idx + 1
                key_r = alloc_reg()
                emit(0, key_r, add_const(arr_idx))
                local vrk = parse_expr(0); cur().reg_top = save
                val_r = alloc_reg()
                local vs = to_reg(vrk, val_r)
                if vs ~= val_r then emit(3, val_r, vs) end
                emit(7, tbl_r, key_r, val_r)
            end

            cur().reg_top = save + 1
            if not lex:match("op", ",") then lex:match("op", ";") end
        end

        lex:expect("op", "}")
        return tbl_r
    end

    local function compile_function_body(param_names, is_vararg)
        push_ctx(#param_names, is_vararg)
        local c = cur()
        c.reg_top = 0
        for i, name in ipairs(param_names) do
            c.locals[name] = i - 1
            c.reg_top = i
        end

        parse_block({"end"})
        lex:expect("kw", "end")

        local ins = c.instructions
        if #ins == 0 or ins[#ins].op ~= 36 then
            emit(36, 0, 1)
        end

        local ctx = pop_ctx()
        local proto = {
            instructions = ctx.instructions,
            params       = ctx.params,
            is_vararg    = ctx.is_vararg,
            upvalues     = ctx.upvalues or {},
        }
        return add_proto(proto)
    end

    parse_expr = function(min_prec)
        min_prec = min_prec or 0

        local function primary()
            local t = lex:peek()
            if not t then error("Unexpected end of source in expression") end

            if t.type == "kw" and t.val == "not" then
                lex:next()
                local rk = primary(); local r = to_reg(rk)
                local dst = alloc_reg(); emit(30, dst, Rrk(r))
                return Rrk(dst)
            end

            if t.type == "op" and t.val == "-" then
                lex:next()
                local rk = primary(); local r = to_reg(rk)
                local dst = alloc_reg(); emit(15, dst, Rrk(r))
                return Rrk(dst)
            end

            if t.type == "op" and t.val == "~" then
                lex:next()
                local rk = primary(); local r = to_reg(rk)
                local dst = alloc_reg(); emit(20, dst, Rrk(r))
                return Rrk(dst)
            end

            if t.type == "op" and t.val == "#" then
                lex:next()
                local rk = primary(); local r = to_reg(rk)
                local dst = alloc_reg(); emit(16, dst, Rrk(r))
                return Rrk(dst)
            end

            if t.type == "kw" and t.val == "true"  then lex:next(); local r = alloc_reg(); emit(1, r, 1); return Rrk(r) end
            if t.type == "kw" and t.val == "false" then lex:next(); local r = alloc_reg(); emit(1, r, 0); return Rrk(r) end
            if t.type == "kw" and t.val == "nil"   then lex:next(); local r = alloc_reg(); emit(2, r);    return Rrk(r) end

            if t.type == "op" and t.val == "..." then
                lex:next(); local dst = alloc_reg(); emit(38, dst, 2); return Rrk(dst)
            end

            if t.type == "number" or t.type == "string" then
                lex:next(); return Krk(add_const(t.val))
            end

            if t.type == "op" and t.val == "{" then
                return Rrk(parse_table_constructor())
            end

            if t.type == "kw" and t.val == "function" then
                lex:next(); lex:expect("op", "(")
                local params = {}; local is_vararg = false
                if not lex:check("op", ")") then
                    repeat
                        if lex:check("op", "...") then lex:next(); is_vararg = true; break end
                        params[#params+1] = lex:expect("id").val
                    until not lex:match("op", ",")
                end
                lex:expect("op", ")")
                local proto_idx = compile_function_body(params, is_vararg)
                local dst = alloc_reg(); emit(34, dst, proto_idx)
                return Rrk(dst)
            end

            if t.type == "op" and t.val == "(" then
                lex:next(); local rk = parse_expr(0); lex:expect("op", ")"); return rk
            end

            if t.type == "id" and t.val == "KRS_NOVIRTUALIZE" then
                lex:next()
                lex:expect("op", "(")
                local func_tok = lex:peek()
                if not (func_tok and func_tok.type == "kw" and func_tok.val == "function") then
                    error("KRS_NOVIRTUALIZE expects a function expression")
                end
                local body_start = func_tok.start
                lex:next()
                lex:expect("op", "(")
                local params = {}; local is_vararg = false
                if not lex:check("op", ")") then
                    repeat
                        if lex:check("op", "...") then lex:next(); is_vararg = true; break end
                        params[#params+1] = lex:expect("id").val
                    until not lex:match("op", ",")
                end
                lex:expect("op", ")")
                local prev_suppress = suppress_protos
                suppress_protos = true
                push_ctx(#params, is_vararg)
                local c = cur()
                c.reg_top = 0
                for i, name in ipairs(params) do
                    c.locals[name] = i - 1
                    c.reg_top = i
                end
                parse_block({"end"})
                local end_tok = lex:expect("kw", "end")
                pop_ctx()
                suppress_protos = prev_suppress
                local body_finish = end_tok.finish
                local body_src = source:sub(body_start, body_finish - 1)
                local NOVIRTUALIZE_name = gen_id()
                local decoder_name = "KRS_B64D"
                local rewritten, had_str = encode_NOVIRTUALIZE_strings(body_src, decoder_name)
                NOVIRTUALIZEs[#NOVIRTUALIZEs + 1] = {
                    name = NOVIRTUALIZE_name,
                    body = rewritten,
                    needs_b64 = had_str,
                }
                lex:expect("op", ")")
                local dst = alloc_reg()
                emit(4, dst, add_const(NOVIRTUALIZE_name))
                return Rrk(dst)
            end

            if t.type == "id" and t.val == "KRS_ENCSTR" then
                lex:next()
                lex:expect("op", "(")
                local st = lex:peek()
                if not (st and st.type == "string") then
                    error("KRS_ENCSTR expects a string literal")
                end
                lex:next()
                lex:expect("op", ")")
                return Krk(add_const(st.val))
            end

            if t.type == "id" and t.val == "KRS_ENCNUM" then
                lex:next()
                lex:expect("op", "(")
                local neg = false
                if lex:check("op", "-") then
                    lex:next()
                    neg = true
                end
                local nt = lex:peek()
                if not (nt and nt.type == "number") then
                    error("KRS_ENCNUM expects a number literal")
                end
                lex:next()
                lex:expect("op", ")")
                local num = neg and -nt.val or nt.val
                return Krk(add_const({ __krs_encnum = true, v = num }))
            end

            if t.type == "id" then
                lex:next()
                local name = t.val
                local loc, kind = lookup_local(name)
                local base_r
                if kind == "local" then
                    base_r = loc
                elseif kind == "upval" then
                    base_r = alloc_reg()
                    emit(42, base_r, loc)
                else
                    base_r = alloc_reg(); emit(4, base_r, add_const(name))
                end

                while true do
                    if lex:check("op", "[") then
                        lex:next()
                        local save  = cur().reg_top
                        local krk   = parse_expr(0); cur().reg_top = save
                        local key_r = to_reg(krk)
                        lex:expect("op", "]")
                        local dst = alloc_reg(); emit(6, dst, base_r, key_r)
                        base_r = dst

                    elseif lex:check("op", ".") then
                        lex:next()
                        local field = lex:expect("id").val
                        local key_r = alloc_reg()
                        emit(0, key_r, add_const(field))
                        local dst = alloc_reg(); emit(6, dst, base_r, key_r)
                        base_r = dst

                    elseif lex:check("op", ":") then
                        lex:next()
                        local method = lex:expect("id").val
                        local key_r  = alloc_reg(); emit(0, key_r, add_const(method))
                        local fn_r   = alloc_reg(); emit(6, fn_r, base_r, key_r)
                        lex:expect("op", "(")
                        local call_base = alloc_reg(); emit(3, call_base, fn_r)
                        local self_slot = alloc_reg(); emit(3, self_slot, base_r)
                        local extra = emit_args(self_slot + 1)
                        emit(35, call_base, extra + 2, 2)
                        cur().reg_top = call_base + 1
                        base_r = call_base

                    elseif lex:check("op", "(") then
                        lex:next()
                        local call_base = alloc_reg(); emit(3, call_base, base_r)
                        local argc = emit_args(call_base + 1)
                        emit(35, call_base, argc + 1, 2)
                        cur().reg_top = call_base + 1
                        base_r = call_base

                    else
                        break
                    end
                end
                return Rrk(base_r)
            end

            error("Unexpected token in expression: " .. tostring(t.val or t.type))
        end

        local lhs = primary()

        while true do
            local t = lex:peek(); if not t then break end
            local op   = t.val
            local prec = BINOP_PREC[op]
            if not prec or prec <= min_prec then break end
            lex:next()

            if op == "or" then
                local dst   = alloc_reg()
                local lhs_r = to_reg(lhs, dst)
                if lhs_r ~= dst then emit(3, dst, lhs_r) end
                local tmp   = alloc_reg()
                emit(30, tmp, Rrk(dst))
                local jmp_i = emit(33, tmp, 0)
                cur().reg_top = dst + 1
                local rhs   = parse_expr(prec)
                local rhs_r = to_reg(rhs, dst)
                if rhs_r ~= dst then emit(3, dst, rhs_r) end
                patch(jmp_i, "b", #cur().instructions - jmp_i)
                cur().reg_top = tmp
                lhs = Rrk(dst)

            elseif op == "and" then
                local dst   = alloc_reg()
                local lhs_r = to_reg(lhs, dst)
                if lhs_r ~= dst then emit(3, dst, lhs_r) end
                local jmp_i = emit(33, dst, 0)
                cur().reg_top = dst + 1
                local rhs   = parse_expr(prec)
                local rhs_r = to_reg(rhs, dst)
                if rhs_r ~= dst then emit(3, dst, rhs_r) end
                patch(jmp_i, "b", #cur().instructions - jmp_i)
                lhs = Rrk(dst)

            else
                local rhs
                if op == "^" then rhs = parse_expr(prec - 1)
                else              rhs = parse_expr(prec)
                end
                local opcode = BINOP_OP[op]
                if opcode then
                    local dst = alloc_reg()
                    emit(opcode, dst, lhs, rhs)
                    lhs = Rrk(dst)
                end
            end
        end

        return lhs
    end

    parse_stat = function()
        local t = lex:peek()
        if not t then return false end

        if t.type == "op" and t.val == ";" then lex:next(); return true end

        if t.type == "id" and t.val == "print" then
            lex:next(); lex:expect("op", "(")
            local base_r = cur().reg_top
            local count  = 0
            repeat
                local dst  = base_r + count
                if cur().reg_top <= dst then cur().reg_top = dst + 1 end
                local save = cur().reg_top
                local rk   = parse_expr(0); cur().reg_top = save
                local src  = to_reg(rk, dst)
                if src ~= dst then emit(3, dst, src) end
                count = count + 1
            until not lex:match("op", ",")
            lex:expect("op", ")")
            emit(31, base_r, count)
            cur().reg_top = base_r
            return true
        end

        if t.type == "kw" and t.val == "return" then
            lex:next()
            if lex:check("kw","end") or lex:check("kw","else") or lex:check("kw","elseif")
            or lex:check("kw","until") or lex:peek() == nil then
                emit(36, 0, 1)
            else
                local base_r = cur().reg_top
                local count  = 0
                repeat
                    local dst = base_r + count
                    if cur().reg_top <= dst then cur().reg_top = dst + 1 end
                    local rk  = parse_expr(0)
                    local src = to_reg(rk, dst)
                    if src ~= dst then emit(3, dst, src) end
                    count = count + 1
                until not lex:match("op", ",")
                emit(36, base_r, count + 1)
            end
            lex:match("op", ";")
            return true
        end

        if t.type == "kw" and t.val == "local" then
            lex:next()

            if lex:check("kw", "function") then
                lex:next()
                local name = lex:expect("id").val
                local dst  = alloc_reg()
                cur().locals[name] = dst
                lex:expect("op", "(")
                local params = {}; local is_vararg = false
                if not lex:check("op", ")") then
                    repeat
                        if lex:check("op", "...") then lex:next(); is_vararg = true; break end
                        params[#params+1] = lex:expect("id").val
                    until not lex:match("op", ",")
                end
                lex:expect("op", ")")
                local proto_idx = compile_function_body(params, is_vararg)
                emit(34, dst, proto_idx)
                return true
            end

            local names = {}
            names[1] = lex:expect("id").val
            while lex:match("op", ",") do names[#names+1] = lex:expect("id").val end

            if lex:match("op", "=") then
                if #names == 1 then
                    local dst = alloc_reg()
                    cur().locals[names[1]] = dst
                    local save = cur().reg_top
                    local rk   = parse_expr(0); cur().reg_top = save
                    local src  = to_reg(rk, dst)
                    if src ~= dst then emit(3, dst, src) end
                else
                    local dsts = {}
                    for i, name in ipairs(names) do
                        dsts[i] = alloc_reg()
                        cur().locals[name] = dsts[i]
                    end
                    local rhs_list = {}
                    repeat
                        rhs_list[#rhs_list+1] = parse_expr(0)
                    until not lex:match("op", ",")

                    local ins  = cur().instructions
                    local last = ins[#ins]
                    if last and last.op == 35 and #rhs_list == 1 then
                        last.c = #names + 1
                        for i = 1, #names do
                            local src_r = last.a + i - 1
                            if src_r ~= dsts[i] then emit(3, dsts[i], src_r) end
                        end
                    else
                        for i, rk in ipairs(rhs_list) do
                            if dsts[i] then
                                local src = to_reg(rk, dsts[i])
                                if src ~= dsts[i] then emit(3, dsts[i], src) end
                            end
                        end
                        for i = #rhs_list + 1, #names do
                            emit(2, dsts[i])
                        end
                    end
                end
            else
                for _, name in ipairs(names) do
                    local dst = alloc_reg()
                    cur().locals[name] = dst
                    emit(2, dst)
                end
            end
            return true
        end

        if t.type == "kw" and t.val == "function" then
            lex:next()
            local name = lex:expect("id").val
            lex:expect("op", "(")
            local params = {}; local is_vararg = false
            if not lex:check("op", ")") then
                repeat
                    if lex:check("op", "...") then lex:next(); is_vararg = true; break end
                    params[#params+1] = lex:expect("id").val
                until not lex:match("op", ",")
            end
            lex:expect("op", ")")
            local proto_idx = compile_function_body(params, is_vararg)
            local fn_r = alloc_reg()
            emit(34, fn_r, proto_idx)
            emit(5, add_const(name), fn_r)
            cur().reg_top = fn_r
            return true
        end

        if t.type == "kw" and t.val == "if" then
            lex:next()
            local save     = cur().reg_top
            local cond_rk  = parse_expr(0); cur().reg_top = save
            local cond_r   = to_reg(cond_rk)
            lex:expect("kw", "then")

            local jmp_i    = emit(33, cond_r, 0)
            local end_jmps = {}

            parse_block({"else","elseif","end"})

            while lex:check("kw", "elseif") do
                local skip = emit(32, 0)
                end_jmps[#end_jmps+1] = skip
                patch(jmp_i, "b", #cur().instructions - jmp_i)
                lex:next()
                save    = cur().reg_top
                cond_rk = parse_expr(0); cur().reg_top = save
                cond_r  = to_reg(cond_rk)
                lex:expect("kw", "then")
                jmp_i   = emit(33, cond_r, 0)
                parse_block({"else","elseif","end"})
            end

            if lex:match("kw", "else") then
                local skip = emit(32, 0)
                end_jmps[#end_jmps+1] = skip
                patch(jmp_i, "b", #cur().instructions - jmp_i)
                parse_block({"end"})
            else
                patch(jmp_i, "b", #cur().instructions - jmp_i)
            end

            local ep = #cur().instructions
            for _, ji in ipairs(end_jmps) do
                patch(ji, "a", ep - ji)
            end

            lex:expect("kw", "end")
            return true
        end

        if t.type == "kw" and t.val == "while" then
            lex:next()
            local loop_top = #cur().instructions + 1
            local save     = cur().reg_top
            local cond_rk  = parse_expr(0); cur().reg_top = save
            local cond_r   = to_reg(cond_rk)
            lex:expect("kw", "do")
            local jmp_i = emit(33, cond_r, 0)
            local saved_breaks = cur().break_patches
            cur().break_patches = {}
            local save2 = cur().reg_top
            parse_block({"end"}); cur().reg_top = save2
            emit(32, loop_top - #cur().instructions - 2)
            patch(jmp_i, "b", #cur().instructions - jmp_i)
            local ep = #cur().instructions
            for _, bi in ipairs(cur().break_patches) do
                patch(bi, "a", ep - bi)
            end
            cur().break_patches = saved_breaks
            lex:expect("kw", "end")
            return true
        end

        if t.type == "kw" and t.val == "repeat" then
            lex:next()
            local loop_top = #cur().instructions + 1
            local save     = cur().reg_top
            local saved_breaks = cur().break_patches
            cur().break_patches = {}
            parse_block({"until"}); cur().reg_top = save
            lex:expect("kw", "until")
            local cond_rk = parse_expr(0)
            local cond_r  = to_reg(cond_rk)
            emit(33, cond_r, loop_top - #cur().instructions - 2)
            local ep = #cur().instructions
            for _, bi in ipairs(cur().break_patches) do
                patch(bi, "a", ep - bi)
            end
            cur().break_patches = saved_breaks
            return true
        end

        if t.type == "kw" and t.val == "for" then
            lex:next()
            local var = lex:expect("id").val

            if lex:check("op", ",") or lex:check("kw", "in") then
                local vars = {var}
                while lex:match("op", ",") do vars[#vars+1] = lex:expect("id").val end
                lex:expect("kw", "in")

                local f_r = alloc_reg()
                local s_r = alloc_reg()
                local c_r = alloc_reg()
                local save = cur().reg_top
                local rk = parse_expr(0)
                local ins = cur().instructions
                local last = ins[#ins]
                if last and last.op == 35 then
                    last.c = 4
                    local base = last.a
                    cur().reg_top = math.max(cur().reg_top, base + 3)
                    if base ~= f_r then emit(3, f_r, base) end
                    emit(3, s_r, base + 1)
                    emit(3, c_r, base + 2)
                else
                    local sr = to_reg(rk, f_r)
                    if sr ~= f_r then emit(3, f_r, sr) end
                    emit(2, s_r)
                    emit(2, c_r)
                end
                cur().reg_top = math.max(cur().reg_top, c_r + 1)

                local var_regs = {}
                for i, v in ipairs(vars) do
                    local r = alloc_reg()
                    cur().locals[v] = r
                    var_regs[i] = r
                end

                lex:expect("kw", "do")
                local loop_top = #cur().instructions + 1

                local call_base = alloc_reg()
                emit(3, call_base, f_r)
                emit(3, call_base + 1, s_r)
                emit(3, call_base + 2, c_r)
                emit(35, call_base, 3, #vars + 1)
                for i, r in ipairs(var_regs) do
                    emit(3, r, call_base + i - 1)
                end
                emit(3, c_r, var_regs[1])
                local exit_jmp = emit(33, var_regs[1], 0)

                local saved_breaks = cur().break_patches
                cur().break_patches = {}
                local save2 = cur().reg_top
                parse_block({"end"}); cur().reg_top = save2
                emit(32, loop_top - #cur().instructions - 2)
                patch(exit_jmp, "b", #cur().instructions - exit_jmp)
                local ep = #cur().instructions
                for _, bi in ipairs(cur().break_patches) do
                    patch(bi, "a", ep - bi)
                end
                cur().break_patches = saved_breaks
                lex:expect("kw", "end")
                return true
            end

            lex:expect("op", "=")
            local r_var   = alloc_reg()
            local r_limit = alloc_reg()
            local r_step  = alloc_reg()

            local save = cur().reg_top
            local rk   = parse_expr(0); cur().reg_top = save
            local sr   = to_reg(rk, r_var); if sr ~= r_var then emit(3, r_var, sr) end

            lex:expect("op", ",")
            save = cur().reg_top
            rk   = parse_expr(0); cur().reg_top = save
            sr   = to_reg(rk, r_limit); if sr ~= r_limit then emit(3, r_limit, sr) end

            if lex:match("op", ",") then
                save = cur().reg_top
                rk   = parse_expr(0); cur().reg_top = save
                sr   = to_reg(rk, r_step); if sr ~= r_step then emit(3, r_step, sr) end
            else
                emit(0, r_step, add_const(1))
            end

            cur().locals[var] = r_var
            lex:expect("kw", "do")

            local prep_idx = emit(39, r_var, r_limit, 0, r_step)
            local loop_top = #cur().instructions + 1
            local saved_breaks = cur().break_patches
            cur().break_patches = {}
            local save2    = cur().reg_top
            parse_block({"end"}); cur().reg_top = save2
            local step_idx = emit(40, r_var, r_limit, 0, r_step)
            patch(step_idx, "c", loop_top - step_idx - 1)
            patch(prep_idx, "c", step_idx - prep_idx)
            local ep = #cur().instructions
            for _, bi in ipairs(cur().break_patches) do
                patch(bi, "a", ep - bi)
            end
            cur().break_patches = saved_breaks
            lex:expect("kw", "end")
            return true
        end

        if t.type == "kw" and t.val == "do" then
            lex:next()
            local save         = cur().reg_top
            local saved_locals = {}
            for k, v in pairs(cur().locals) do saved_locals[k] = v end
            parse_block({"end"})
            cur().reg_top = save
            for k in pairs(cur().locals) do cur().locals[k] = saved_locals[k] end
            lex:expect("kw", "end")
            return true
        end

        if t.type == "kw" and t.val == "break" then
            lex:next()
            local jmp_i = emit(32, 0)
            local patches = cur().break_patches
            if patches then
                patches[#patches+1] = jmp_i
            end
            lex:match("op", ";")
            return true
        end

        if t.type == "id" then
            lex:next()
            local name    = t.val
            local loc, kind = lookup_local(name)
            local base_r
            if kind == "local" then
                base_r = loc
            elseif kind == "upval" then
                base_r = alloc_reg()
                emit(42, base_r, loc)
            else
                base_r = alloc_reg(); emit(4, base_r, add_const(name))
            end

            local chain = {}
            local was_call = false

            while true do
                if lex:check("op", "[") then
                    if was_call and #chain > 0 then
                        for _, op in ipairs(chain) do
                            local dst = alloc_reg(); emit(6, dst, base_r, op.key_r); base_r = dst
                        end
                        chain = {}
                    end
                    was_call = false
                    lex:next()
                    local save  = cur().reg_top
                    local krk   = parse_expr(0); cur().reg_top = save
                    local key_r = to_reg(krk)
                    lex:expect("op", "]")
                    chain[#chain+1] = {type="index", key_r=key_r}

                elseif lex:check("op", ".") then
                    if was_call and #chain > 0 then
                        for _, op in ipairs(chain) do
                            local dst = alloc_reg(); emit(6, dst, base_r, op.key_r); base_r = dst
                        end
                        chain = {}
                    end
                    was_call = false
                    lex:next()
                    local field = lex:expect("id").val
                    local key_r = alloc_reg()
                    emit(0, key_r, add_const(field))
                    chain[#chain+1] = {type="index", key_r=key_r}

                elseif lex:check("op", ":") then
                    local obj_r = base_r
                    for _, op in ipairs(chain) do
                        local dst = alloc_reg(); emit(6, dst, obj_r, op.key_r); obj_r = dst
                    end
                    chain = {}
                    lex:next()
                    local method = lex:expect("id").val
                    local key_r  = alloc_reg(); emit(0, key_r, add_const(method))
                    local fn_r   = alloc_reg(); emit(6, fn_r, obj_r, key_r)
                    lex:expect("op", "(")
                    local call_base = alloc_reg(); emit(3, call_base, fn_r)
                    local self_slot = alloc_reg(); emit(3, self_slot, obj_r)
                    local argc = emit_args(self_slot + 1)
                    emit(35, call_base, argc + 2, 2)
                    cur().reg_top = call_base + 1
                    base_r = call_base
                    was_call = true

                elseif lex:check("op", "(") then
                    local fn_r = base_r
                    for _, op in ipairs(chain) do
                        local dst = alloc_reg(); emit(6, dst, fn_r, op.key_r); fn_r = dst
                    end
                    chain = {}
                    lex:next()
                    local call_base = alloc_reg(); emit(3, call_base, fn_r)
                    local argc = emit_args(call_base + 1)
                    emit(35, call_base, argc + 1, 2)
                    cur().reg_top = call_base + 1
                    base_r = call_base
                    was_call = true

                else
                    break
                end
            end

            if not was_call and #chain == 0 and lex:check("op", ",") then
                local targets = { {name=name, loc=loc, kind=kind} }
                while lex:match("op", ",") do
                    local n = lex:expect("id").val
                    local l, k = lookup_local(n)
                    targets[#targets+1] = {name=n, loc=l, kind=k}
                end
                lex:expect("op", "=")
                local rhs_list = {}
                repeat
                    rhs_list[#rhs_list+1] = parse_expr(0)
                until not lex:match("op", ",")

                local ins  = cur().instructions
                local last = ins[#ins]
                if last and last.op == 35 and #rhs_list == 1 then
                    last.c = #targets + 1
                    for i, tgt in ipairs(targets) do
                        local src_r = last.a + i - 1
                        if tgt.kind == "local" then
                            if src_r ~= tgt.loc then emit(3, tgt.loc, src_r) end
                        elseif tgt.kind == "upval" then
                            emit(43, tgt.loc, src_r)
                        else
                            emit(5, add_const(tgt.name), src_r)
                        end
                    end
                else
                    for i, tgt in ipairs(targets) do
                        local rk = rhs_list[i]
                        if rk then
                            local src = to_reg(rk)
                            if tgt.kind == "local" then
                                if src ~= tgt.loc then emit(3, tgt.loc, src) end
                            elseif tgt.kind == "upval" then
                                emit(43, tgt.loc, src)
                            else
                                emit(5, add_const(tgt.name), src)
                            end
                        else
                            if tgt.kind == "local" then
                                emit(2, tgt.loc)
                            elseif tgt.kind == "upval" then
                                local tmp = alloc_reg(); emit(2, tmp); emit(43, tgt.loc, tmp)
                            else
                                local tmp = alloc_reg(); emit(2, tmp); emit(5, add_const(tgt.name), tmp)
                            end
                        end
                    end
                end
                return true
            end

            if not was_call and lex:check("op", "=") then
                lex:next()
                local save  = cur().reg_top
                local rk    = parse_expr(0); cur().reg_top = save
                local val_r = to_reg(rk)

                if #chain > 0 then
                    local cur_r = base_r
                    for i = 1, #chain - 1 do
                        local dst = alloc_reg()
                        emit(6, dst, cur_r, chain[i].key_r)
                        cur_r = dst
                    end
                    emit(7, cur_r, chain[#chain].key_r, val_r)
                else
                    if kind == "local" then
                        if val_r ~= loc then emit(3, loc, val_r) end
                    elseif kind == "upval" then
                        emit(43, loc, val_r)
                    else
                        emit(5, add_const(name), val_r)
                    end
                end
                return true
            end

            return true
        end

        return false
    end

    parse_block = function(stop_keywords)
        local stop = {}
        for _, k in ipairs(stop_keywords) do stop[k] = true end

        while true do
            while lex:match("op", ";") do end

            local t = lex:peek()
            if not t then break end
            if t.type == "kw" and stop[t.val] then break end

            if not parse_stat() then break end
        end
    end

    push_ctx(0, true)
    parse_block({})

    local ins = cur().instructions
    if #ins == 0 or (ins[#ins].op ~= 41 and ins[#ins].op ~= 36) then
        emit(41)
    end

    local main_ctx = pop_ctx()
    return main_ctx.instructions, constants, protos, NOVIRTUALIZEs
end


----------------------------------------------------------------------------
-- PSEUDO IR STAGE
----------------------------------------------------------------------------
local PSEUDO_OP_NAMES = {}
for i = 0, NUM_OPCODES - 1 do
    PSEUDO_OP_NAMES[i] = OPCODES[i].name
end

local CONTROL_FLOW = {
    JMP = true, JMPIF = true, FORPREP = true, FORSTEP = true,
    RETURN = true, TAILCALL = true, HALT = true,
}

local function to_pseudo(instructions, constants, protos)
    local function lift_ins_list(ins_list)
        local out = {}
        for _, ins in ipairs(ins_list) do
            out[#out + 1] = {
                op   = PSEUDO_OP_NAMES[ins.op] or ("OP_" .. tostring(ins.op)),
                a    = ins.a or 0,
                b    = ins.b or 0,
                c    = ins.c or 0,
                d    = ins.d or 0,
                meta = {},
            }
        end
        return out
    end

    local pseudo_protos = {}
    local max_p = -1
    for k in pairs(protos) do if k > max_p then max_p = k end end
    for i = 0, max_p do
        local p = protos[i]
        if p then
            pseudo_protos[i] = {
                params       = p.params or 0,
                is_vararg    = p.is_vararg or false,
                upvalues     = p.upvalues or {},
                instructions = lift_ins_list(p.instructions or {}),
            }
        end
    end

    return {
        instructions = lift_ins_list(instructions or {}),
        constants    = constants,
        protos       = pseudo_protos,
    }
end

----------------------------------------------------------------------------
-- IR TRANSFORMS
----------------------------------------------------------------------------
local JUNK_REG_BASE = 200

local function make_ins(op, a, b, c, d)
    return { op = op, a = a or 0, b = b or 0, c = c or 0, d = d or 0, meta = { junk = true } }
end

local function expand_loadk(ins, constants)
    if ins.op ~= "LOADK" then return {ins} end

    local dst  = ins.a
    local kidx = ins.b
    local val  = constants and constants[kidx]

    if type(val) == "table" and val.__krs_encnum then
        return {ins}
    end

    local seq = {}
    local function emit(op, a, b, c, d)
        seq[#seq + 1] = make_ins(op, a, b, c, d)
    end

    if type(val) == "number" and val == math.floor(val) and math.abs(val) <= 32 then
        local n   = val
        local neg = n < 0
        if neg then n = -n end

        local tmp = JUNK_REG_BASE + 4

        emit("NEWTABLE", tmp)
        emit("LEN", dst, tmp * 2)

        if n > 0 then
            local one_idx = nil
            for i, c in pairs(constants) do
                if c == 1 then one_idx = i; break end
            end
            if not one_idx then return {ins} end

            emit("LOADK", tmp, one_idx)
            for _ = 1, n do
                emit("ADD", dst, dst * 2, tmp * 2)
            end
        end

        if neg then
            emit("NEG", dst, dst * 2)
        end
        return seq
    end

    if type(val) == "string" and #val >= 1 and #val <= 10 then
        local str_idx, char_idx = nil, nil
        for i, c in pairs(constants) do
            if c == "string" then str_idx = i end
            if c == "char"   then char_idx = i end
        end
        if not str_idx or not char_idx then
            return {ins}
        end

        local tmpS  = JUNK_REG_BASE + 5
        local tmpC  = JUNK_REG_BASE + 6
        local tmpB  = JUNK_REG_BASE + 7
        local tmpA  = JUNK_REG_BASE + 8

        emit("GETENV", tmpS, str_idx)
        emit("LOADK", tmpB, char_idx)
        emit("GETTABLE", tmpC, tmpS, tmpB)

        local first = true
        for i = 1, #val do
            local byte = val:byte(i)
            local byte_idx = nil
            for ci, c in pairs(constants) do
                if c == byte then byte_idx = ci; break end
            end
            if not byte_idx then
                return {ins}
            end

            emit("LOADK", tmpB, byte_idx)

            local call_base = JUNK_REG_BASE + 9
            emit("MOVE", call_base, tmpC)
            emit("MOVE", call_base + 1, tmpB)
            emit("CALL", call_base, 2, 2)

            if first then
                emit("MOVE", tmpA, call_base)
                first = false
            else
                emit("CONCAT", tmpA, tmpA * 2, call_base * 2)
            end
        end

        emit("MOVE", dst, tmpA)
        return seq
    end

    return {ins}
end

local function make_junk_sequence()
    local regs = {0,1,2,3,4,5,6,7,8}
    for i = #regs, 2, -1 do
        local j = math.random(1, i)
        regs[i], regs[j] = regs[j], regs[i]
    end
    local r1 = JUNK_REG_BASE + regs[1]
    local r2 = JUNK_REG_BASE + regs[2]
    local r3 = JUNK_REG_BASE + regs[3]

    local kind = math.random(1, 8)
    if kind == 1 then
        return { make_ins("LOADNIL", r1) }
    elseif kind == 2 then
        return { make_ins("LOADBOOL", r1, math.random(0, 1)) }
    elseif kind == 3 then
        return {
            make_ins("LOADNIL", r1),
            make_ins("MOVE", r2, r1),
        }
    elseif kind == 4 then
        return {
            make_ins("LOADBOOL", r1, 1),
            make_ins("NOT", r2, r1 * 2),
            make_ins("MOVE", r3, r2),
        }
    elseif kind == 5 then
        return {
            make_ins("NEWTABLE", r1),
            make_ins("LEN", r2, r1 * 2),
        }
    elseif kind == 6 then
        return {
            make_ins("LOADNIL", r1),
            make_ins("LOADNIL", r2),
            make_ins("MOVE", r3, r1),
        }
    elseif kind == 7 then
        return { make_ins("MOVE", r1, r1) }
    else
        return {
            make_ins("NEWTABLE", r1),
            make_ins("LOADBOOL", r2, 1),
            make_ins("LOADBOOL", r3, 0),
            make_ins("SETTABLE", r1, r2, r3),
        }
    end
end

local function substitute_instruction(ins)
    local op = ins.op
    if op == "MOVE" and math.random() < 0.35 then
        local tmp = JUNK_REG_BASE + math.random(0, 5)
        return {
            make_ins("LOADNIL", tmp),
            make_ins("MOVE", tmp, ins.b),
            make_ins("MOVE", ins.a, tmp),
        }
    end
    if op == "LOADBOOL" and ins.b == 1 and math.random() < 0.3 then
        local tmp = JUNK_REG_BASE + math.random(0, 5)
        return {
            make_ins("LOADBOOL", tmp, 0),
            make_ins("NOT", ins.a, tmp * 2),
        }
    end
    if op == "LOADBOOL" and ins.b == 0 and math.random() < 0.3 then
        local tmp = JUNK_REG_BASE + math.random(0, 5)
        return {
            make_ins("LOADBOOL", tmp, 1),
            make_ins("NOT", ins.a, tmp * 2),
        }
    end
    if op == "NOT" and math.random() < 0.4 then
        local t1 = JUNK_REG_BASE + math.random(0, 4)
        local t2 = JUNK_REG_BASE + math.random(5, 8)
        return {
            make_ins("NOT", t1, ins.b),
            make_ins("NOT", t2, t1 * 2),
            make_ins("NOT", ins.a, t2 * 2),
        }
    end
    return nil
end

local function try_super(list, i)
    local a = list[i]
    local b = list[i + 1]
    if not a or not b then return nil end
    if CONTROL_FLOW[a.op] or CONTROL_FLOW[b.op] then return nil end

    if a.op == "LOADK" and b.op == "MOVE" and a.a == b.b and math.random() < 0.55 then
        return { make_ins("S_LOADK_MOVE", b.a, a.b, b.a) }, 2
    end
    if a.op == "MOVE" and b.op == "MOVE" and a.b == b.b and math.random() < 0.45 then
        return { make_ins("S_MOVE2", a.a, a.b, b.a) }, 2
    end
    if a.op == "LOADNIL" and b.op == "LOADNIL" and math.random() < 0.50 then
        return { make_ins("S_LOADNIL2", a.a, b.a) }, 2
    end
    if a.op == "NOT" and b.op == "NOT" and (a.a * 2) == b.b and math.random() < 0.50 then
        return { make_ins("S_NOT_NOT", b.a, a.b) }, 2
    end
    if a.op == "LOADK" and (b.op == "ADD" or b.op == "SUB" or b.op == "MUL" or b.op == "DIV") then
        local opmap = { ADD = 9, SUB = 10, MUL = 11, DIV = 12 }
        if b.c % 2 == 1 and ((b.c - 1) // 2) == a.a and math.random() < 0.50 then
            return { make_ins("S_ARITH_K", b.a, b.b, a.b, opmap[b.op]) }, 2
        end
    end
    if a.op == "LOADK" and b.op == "GETTABLE" and b.c == a.a and math.random() < 0.60 then
        return { make_ins("S_GETTABLE_K", b.a, b.b, a.b) }, 2
    end
    if a.op == "LOADK" and b.op == "SETTABLE" and b.b == a.a and math.random() < 0.60 then
        return { make_ins("S_SETTABLE_K", b.a, a.b, b.c) }, 2
    end
    if a.op == "LOADK" and b.op == "LOADK" and math.random() < 0.45 then
        return { make_ins("S_LOADK2", a.a, a.b, b.a, b.b) }, 2
    end
    if a.op == "MOVE" and b.op == "LOADK" and math.random() < 0.40 then
        return { make_ins("S_MOVE_LOADK", a.a, a.b, b.a, b.b) }, 2
    end
    return nil
end

local function transform_ins_list(list, constants)
    if not list or #list == 0 then return list end
    constants = constants or {}

    local old_to_new = {}
    local out = {}

    local function emit_junk_into(dest)
        if math.random() >= 0.50 then return end
        local junk = make_junk_sequence()
        for _, j in ipairs(junk) do dest[#dest + 1] = j end
    end

    if math.random() < 0.55 then
        local prefix = make_junk_sequence()
        for _, j in ipairs(prefix) do out[#out + 1] = j end
    end

    local i = 1
    while i <= #list do
        local ins = list[i]

        local prev = list[i - 1]
        if prev and not CONTROL_FLOW[prev.op] and not CONTROL_FLOW[ins.op] then
            emit_junk_into(out)
        end

        if ins.op == "LOADK" then
            local expanded = expand_loadk(ins, constants)
            old_to_new[i] = #out + 1
            for _, s in ipairs(expanded) do out[#out + 1] = s end
            i = i + 1
        else
            local super, consumed = try_super(list, i)
            if super then
                old_to_new[i] = #out + 1
                for k = 1, consumed - 1 do
                    old_to_new[i + k] = old_to_new[i]
                end
                for _, s in ipairs(super) do out[#out + 1] = s end
                i = i + consumed
            elseif not CONTROL_FLOW[ins.op] then
                local sub = substitute_instruction(ins)
                if sub then
                    old_to_new[i] = #out + 1
                    for _, s in ipairs(sub) do out[#out + 1] = s end
                else
                    old_to_new[i] = #out + 1
                    out[#out + 1] = ins
                end
                i = i + 1
            else
                old_to_new[i] = #out + 1
                out[#out + 1] = {
                    op = ins.op, a = ins.a, b = ins.b, c = ins.c, d = ins.d, meta = ins.meta or {}
                }
                i = i + 1
            end
        end
    end

    for old_pc, new_pc in pairs(old_to_new) do
        local ins = out[new_pc]
        if ins then
            local function patch_rel(field)
                local old_rel = ins[field]
                if type(old_rel) ~= "number" then return end
                local old_target = old_pc + 1 + old_rel
                local new_target = old_to_new[old_target]
                if not new_target then return end
                ins[field] = new_target - new_pc - 1
            end

            if ins.op == "JMP" then
                patch_rel("a")
            elseif ins.op == "JMPIF" then
                patch_rel("b")
            elseif ins.op == "FORPREP" or ins.op == "FORSTEP" then
                patch_rel("c")
            end
        end
    end

    return out
end

local function transform_pseudo(pseudo)
    local consts = pseudo.constants or {}
    pseudo.instructions = transform_ins_list(pseudo.instructions, consts)
    for i, pp in pairs(pseudo.protos) do
        pp.instructions = transform_ins_list(pp.instructions, consts)
    end
    return pseudo
end

local function lower_pseudo(pseudo)
    local name_to_op = {}
    for i = 0, NUM_OPCODES - 1 do
        name_to_op[OPCODES[i].name] = i
    end

    local function lower_ins_list(pseudo_list)
        local out = {}
        for _, pins in ipairs(pseudo_list) do
            local opcode = name_to_op[pins.op]
            if opcode == nil then
                error("Unknown pseudo opcode: " .. tostring(pins.op))
            end
            out[#out + 1] = {
                op = opcode,
                a  = pins.a or 0,
                b  = pins.b or 0,
                c  = pins.c or 0,
                d  = pins.d or 0,
            }
        end
        return out
    end

    local protos = {}
    for i, pp in pairs(pseudo.protos) do
        protos[i] = {
            params       = pp.params or 0,
            is_vararg    = pp.is_vararg or false,
            upvalues     = pp.upvalues or {},
            instructions = lower_ins_list(pp.instructions or {}),
        }
    end

    return lower_ins_list(pseudo.instructions or {}),
           pseudo.constants,
           protos
end


local function u32(x)
    return x % 4294967296
end
local function bx(a, b)
    return (a ~ b) % 4294967296
end
local function rsh(x, n)
    return (x >> n) % 4294967296
end

local function rc4(key, data)
    local S = {}
    for i = 0, 255 do S[i] = i end
    local j = 0
    local keylen = #key
    for i = 0, 255 do
        j = (j + S[i] + key:byte((i % keylen) + 1)) % 256
        S[i], S[j] = S[j], S[i]
    end
    local i, j = 0, 0
    local out = {}
    for k = 1, #data do
        i = (i + 1) % 256
        j = (j + S[i]) % 256
        S[i], S[j] = S[j], S[i]
        local t = (S[i] + S[j]) % 256
        out[k] = string.char(data:byte(k) ~ S[t])
    end
    return table.concat(out)
end

local function const_encrypt(s, idx, key)
    local function mix(x)
        x = u32(x)
        x = u32(bx(x, rsh(x, 16)))
        x = u32(x + 0x7ed55d16)
        x = u32(bx(x, rsh(x, 13)))
        x = u32(x + 0xc761c23c)
        x = u32(bx(x, rsh(x, 16)))
        x = u32(x + 0x165667b1)
        return x
    end
    local state = mix(u32(key + idx * 2654435761 + 0x9e3779b9))
    local keystr = {}
    for i = 1, 16 do
        state = mix(u32(state + i * 0x9e3779b9))
        keystr[i] = string.char(state % 256)
    end
    keystr = table.concat(keystr)
    local padlen = math.random(5, 15)
    local pad = {}
    for i = 1, padlen do
        pad[i] = string.char(math.random(0, 255))
    end
    local data = string.char(padlen) .. table.concat(pad) .. s
    return rc4(keystr, data)
end

local function serialize(instructions,constants,protos,op_perm,const_key,op_key)
    local out={}
    local max_k=-1
    for k in pairs(constants) do if k>max_k then max_k=k end end
    max_k=math.max(max_k,-1)
    out[#out+1]=write_varint(max_k+1)
    for i=0,max_k do
        local v=constants[i]
        local s
        local typ
        if type(v) == "table" and v.__krs_encnum then
            v = v.v
        end
        if type(v)=="string" then
            s = v
            typ = 1
        else
            s = tostring(v)
            typ = 0
        end
        local enc = const_encrypt(s, i, const_key)
        out[#out+1] = string.char(typ)
        out[#out+1] = write_varint(#enc)
        out[#out+1] = enc
    end
    local proto_count=0
    for k in pairs(protos) do if k>=proto_count then proto_count=k+1 end end
    out[#out+1]=write_varint(proto_count)
    local function ser_instructions(ins_list)
        local FMTS={}
        for i=0,NUM_OPCODES-1 do FMTS[i]=OPCODES[i].fmt end
        out[#out+1]=write_varint(#ins_list)
        for idx,ins in ipairs(ins_list) do
            local plain = op_perm[ins.op]
            local k = ((idx * 2654435761 + op_key) % 256)
            out[#out+1]=string.char((plain ~ k) % 256)
            local fmt=FMTS[ins.op]
            for i=1,#fmt do
                local ch=fmt:sub(i,i)
                if ch=="s" then
                    out[#out+1]=write_signed_varint(ins[({"a","b","c","d"})[i]])
                elseif ch=="r" or ch=="u" then
                    out[#out+1]=write_varint(ins[(({"a","b","c","d"})[i])])
                end
            end
        end
    end
    for i=0,proto_count-1 do
        local p=protos[i]
        out[#out+1]=string.char(p.params or 0)
        out[#out+1]=string.char(p.is_vararg and 1 or 0)
        local uvs = p.upvalues or {}
        local uvcount = #uvs
        out[#out+1]=write_varint(uvcount)
        for ui=1,uvcount do
            local uv = uvs[ui]
            out[#out+1]=string.char(uv.instack and 1 or 0)
            out[#out+1]=write_varint(uv.idx or 0)
        end
        ser_instructions(p.instructions)
    end
    ser_instructions(instructions)
    return table.concat(out)
end

local function shuffle_opcodes(seed)
    math.randomseed(seed)
    local perm={}
    for i=0,NUM_OPCODES-1 do perm[i]=i end
    for i=NUM_OPCODES-1,1,-1 do
        local j=math.random(0,i)
        perm[i],perm[j]=perm[j],perm[i]
    end
    return perm
end

local function pack_binary(data)
    local t = {}
    local n = #data
    local i = 1
    while i <= n do
        local b1 = data:byte(i) or 0
        local b2 = (i+1 <= n) and data:byte(i+1) or 0
        local b3 = (i+2 <= n) and data:byte(i+2) or 0
        local b4 = (i+3 <= n) and data:byte(i+3) or 0
        t[#t+1] = b1 + b2 * 256 + b3 * 65536 + b4 * 16777216
        i = i + 4
    end
    return t, n
end

local function pack_to_blob(packed, payload_len)
    local out = {}
    local function put_u32(n)
        out[#out+1] = string.char(
            n % 256,
            math.floor(n / 256) % 256,
            math.floor(n / 65536) % 256,
            math.floor(n / 16777216) % 256
        )
    end
    put_u32(payload_len)
    for i = 1, #packed do
        put_u32(packed[i])
    end
    return table.concat(out)
end

math.randomseed(os.time() + math.floor(os.clock() * 1000000))

local B64_ALPHA = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
local POLY_B64_DEC = nil

local function poly_b64_encode(data)
    local out = {}
    local pad = #data % 3
    for i = 1, #data - pad, 3 do
        local b1, b2, b3 = data:byte(i, i+2)
        local n = b1 * 65536 + b2 * 256 + b3
        out[#out+1] = B64_ALPHA:sub((n >> 18) + 1, (n >> 18) + 1)
        out[#out+1] = B64_ALPHA:sub(((n >> 12) & 63) + 1, ((n >> 12) & 63) + 1)
        out[#out+1] = B64_ALPHA:sub(((n >> 6) & 63) + 1, ((n >> 6) & 63) + 1)
        out[#out+1] = B64_ALPHA:sub((n & 63) + 1, (n & 63) + 1)
    end
    if pad == 1 then
        local b1 = data:byte(#data)
        local n = b1 * 65536
        out[#out+1] = B64_ALPHA:sub((n >> 18) + 1, (n >> 18) + 1)
        out[#out+1] = B64_ALPHA:sub(((n >> 12) & 63) + 1, ((n >> 12) & 63) + 1)
        out[#out+1] = "=="
    elseif pad == 2 then
        local b1, b2 = data:byte(#data-1, #data)
        local n = b1 * 65536 + b2 * 256
        out[#out+1] = B64_ALPHA:sub((n >> 18) + 1, (n >> 18) + 1)
        out[#out+1] = B64_ALPHA:sub(((n >> 12) & 63) + 1, ((n >> 12) & 63) + 1)
        out[#out+1] = B64_ALPHA:sub(((n >> 6) & 63) + 1, ((n >> 6) & 63) + 1)
        out[#out+1] = "="
    end
    return table.concat(out)
end

local function encodeString(inputStr)
    if #inputStr == 0 then
        return "''"
    end
    if not POLY_B64_DEC then
        local encodedParts = {}
        for i = 1, #inputStr do
            local byte = inputStr:byte(i)
            encodedParts[#encodedParts+1] = string.format("'\\x%02X'", byte)
        end
        return table.concat(encodedParts, "..")
    end
    local encoded = poly_b64_encode(inputStr)
    return string.format("%s(%q)", POLY_B64_DEC, encoded)
end

local function R() return gen_id() end
local _tostring=R() local _tonumber=R() local _fenv=R()
    local _bit=R()  local _math=R()   local _string=R()
    local _table=R() local _type=R() local _of=R()


function obf.obfuscate(source,seed)
    seed=seed or os.time()
    math.randomseed(seed)
    do
        local base = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
        local t = {}
        for i = 1, #base do t[i] = base:sub(i, i) end
        for i = #t, 2, -1 do
            local j = math.random(1, i)
            t[i], t[j] = t[j], t[i]
        end
        B64_ALPHA = table.concat(t)
    end
    POLY_B64_DEC = gen_id()
    local ok,instructions,constants,protos,NOVIRTUALIZEs=pcall(function() return compile(source) end)
    if not ok then error("Compile error: "..tostring(instructions)) end
    NOVIRTUALIZEs = NOVIRTUALIZEs or {}

    local pseudo = to_pseudo(instructions, constants, protos)
    pseudo = transform_pseudo(pseudo)
    instructions, constants, protos = lower_pseudo(pseudo)

    do
        local max_k = -1
        for k in pairs(constants) do
            if type(k) == "number" and k > max_k then
                max_k = k
            end
        end
        constants[max_k + 1] = gen_id() .. gen_id() .. tostring(math.random(1000, 99999))
        constants[max_k + 2] = math.random(100000, 999999999)
    end

    local op_perm=shuffle_opcodes(seed)
    local const_key=math.random(0,2^31-1)
    local op_key=math.random(0,2^31-1)
    local integrity_key=math.random(0,2^31-1)
    local raw=serialize(instructions,constants,protos,op_perm,const_key,op_key)
    local compressed=lz_compress(raw)
    local cipher_key=math.random(0,2^31-1)
    local encrypted=xor_cipher(compressed,cipher_key)
    local packed, payload_len = pack_binary(encrypted)
    local blob = pack_to_blob(packed, payload_len)
    local encoded = a85_encode(blob)
    local vPAYLOAD=R() local vKEY=R()
    local vDEA85=R()  local vUNPACK=R()
    local vXORDEC=R()   local vLZDEC=R()  local vRV=R()     local vRS=R()
    local vRCONSTS=R()  local vRCODE=R()  local vEXEC=R()
    local vPERM=R()     local vFMT=R()
    local vNAVIBO=R()   local vTMP=R()    local vCONSTS=R()
    local vCODE=R()     local vP=R()      local vPROTOS=R()
    local vRPROTOS=R()  local vNEWCLOS=R()
    local vCONSTKEY=R() local vCDECRYPT=R() local vOPKEY=R() local vINTKEY=R()
    local fPC=R()       local fSTOP=R()   local fOP=R()
    local fLIM=R()      local fSTP=R()    local fVARARGS=R()
    local fRETVALS=R()
    local fPARAMS=R()   local fISVARARG=R() local fINSTRUCTIONS=R()
    local fUPVALUES=R() local fUPVALS=R()
    local fINSTACK=R() local fIDX=R() local fCELLV=R() local fOPEN=R() local fREG=R() local fREGS=R()
    local H={}
    for i=0,NUM_OPCODES-1 do H[i]=R() end
    local aINS=R()  local aREGS=R()   local aCONSTS=R()
    local aENV=R()  local aFRM=R()    local aPROTOS=R()
    local out={}
    local function wl(s) out[#out+1]=s end
    local function opaque_true(seed_var)
        local a,b = math.random(11,97), math.random(11,97)
        local forms = {
            ("(%d-%d==%d)"):format(a+b, b, a),
            ("(%d+%d==%d)"):format(a, b, a+b),
            ("(#''==0x0)"),
            ("(#({})==0b0)"),
            ("(0b1==0x01)"),
            ("(type(0x0)=='number')"),
            ("(not not true)"),
            ("(#'x'==0x01)"),
            ("(select(0x01,true,false))"),
            ("(type('')=='string')"),
            ("(0x01-0x00==0b1)"),
        }
        if seed_var then
            forms[#forms+1] = ("((%s-%s)==0x0)"):format(seed_var, seed_var)
            forms[#forms+1] = ("(type(%s)~='nil' or true)"):format(seed_var)
            forms[#forms+1] = ("((%s==%s)and(%d-%d==%d))"):format(seed_var, seed_var, a+b, b, a)
        end
        return forms[math.random(1,#forms)]
    end
    local function opaque_false(seed_var)
        local a,b = math.random(11,97), math.random(11,97)
        local forms = {
            ("(%d+%d==%d)"):format(a, b, a+b+math.random(1,7)),
            ("(0x0~=0x0)"),
            ("(#''==0x01)"),
            ("(0b1==0x00)"),
            ("(not true)"),
            ("(type(0)=='string')"),
            ("(#'xy'==0x01)"),
            ("(#({})==0x01)"),
            ("((%d-%d==%d)and(%d+%d==%d))"):format(a,b,a+1,a,b,a+b+1),
            ("(select(0x01,false,true))"),
            ("(type(true)=='number')"),
        }
        if seed_var then
            forms[#forms+1] = ("((%s-%s)~=0x0)"):format(seed_var, seed_var)
            forms[#forms+1] = ("(type(%s)=='nil' and false)"):format(seed_var)
            forms[#forms+1] = ("((%s~=%s)or(%d+%d==%d))"):format(seed_var, seed_var, a, b, a+b+1)
        end
        return forms[math.random(1,#forms)]
    end
    local function emit_junk()
        if math.random() > 0.12 then return end
        local n = math.random(1, 2)
        for _=1,n do
            local kind = math.random(1, 8)
            local v1,v2,v3 = R(), R(), R()
            local a,b,c = math.random(2,50), math.random(2,50), math.random(1,20)
            local d = math.random(3,12)
            if kind == 1 then
                wl(("local %s=%d;%s=%s*%d+%d;%s=%s-%d"):format(v1,a,v1,v1,b,c,v1,v1,math.random(1,11)))
            elseif kind == 2 then
                wl(("local %s=function(%s)return %s;end;"):format(v1, v2, v2))
            elseif kind == 3 then
                wl(("if%sthen local %s=%d;%s=%s+%d;end"):format(opaque_true(), v1, a, v1, v1, b))
            elseif kind == 4 then
                wl(("if%sthen local %s=%d;%s=%s*%d;%s=nil;end"):format(opaque_false(), v1, a, v1, v1, b, v1))
            elseif kind == 5 then
                wl(("do local %s=%d;%s=%s*%d;end"):format(v1, a, v1, v1, b))
            elseif kind == 6 then
                wl(("local %s=(%s and %d or %d)"):format(v1, opaque_true(), a, b+20))
            elseif kind == 7 then
                wl(("for %s=0b1,0x00 do local %s=%s+%d;end"):format(v1, v2, v1, a))
            elseif kind == 8 then
                wl(("if%sthen if%sthen local %s=%d;end;%s=nil;end"):format(opaque_false(), opaque_true(), v1, a, v1))
            end
        end
    end
    local function emit_dead_end()
        if math.random() > 0.4 then return end
        local v1,v2 = R(), R()
        local a,b = math.random(5,40), math.random(2,15)
        local style = math.random(1, 4)
        if style == 1 then
            wl(("if%sthen local %s=%d;%s=%s*%d;end"):format(opaque_false(), v1, a, v1, v1, b))
        elseif style == 2 then
            wl(("do local %s=%d;if(%s*%d-%d~=%d)then %s=%s+%d;end;end"):format(
                v1, a, v1, b, a*b, a*b+math.random(1,5), v1, v1, math.random(1,7)))
        elseif style == 3 then
            wl("while true do if(129-113==12)then print()return;end;break;end")
        else
            wl(("if%sthen local %s=%d;%s=nil;end"):format(opaque_false(), v1, a, v1))
        end
    end

    local function dynamic()
        local a = R()
        local sty = math.random(1, 3)
        if sty == 1 then
            wl(("local %s=%s()[%s] if(%s(%s)~=%s)then %s=0b11-0b1;end"):format(
                a, _fenv, encodeString("game"), _of, a, encodeString("Instance"), _bit))
        elseif sty == 2 then
            local b = R()
            wl(("pcall(function()local %s=workspace[%s] if(%s~=nil)then %s=nil;end;end)"):format(
                b,encodeString("randomasspart"),b,_math))
        else
            local b = R()
            wl(("local %s,%s=pcall(function()return game[%s]end)if(not %s or %s(%s)~=%s)then %s=0b1;end"):format(
                a, b, encodeString("PlaceId"), a, _type, b, encodeString("number"), _string))
        end
    end

    local function anti_line_check()
    local vOK   = R()
    local vERR  = R()
    local vLINE = R()
    local vM    = R()

    -- force error on the current line
    wl(("local %s,%s=pcall(function()error(%s)end)"):format(
        vOK, vERR, encodeString("")))

    -- robust line extraction (works on both Lua and Luau/Roblox)
    wl(("local %s=0x0"):format(vLINE))
    wl(("if(%s)then"):format(vERR))
    -- try the most common patterns
    wl(("  local %s=%s[%s](%s,%s)or %s[%s](%s,%s)or %s[%s](%s,%s)"):format(
        vM,
        vERR, encodeString("match"), vERR, encodeString(":(%d+):"),
        vERR, encodeString("match"), vERR, encodeString(":(%d+)"),
        vERR, encodeString("match"), vERR, encodeString(":(%d+)%s")
    ))
    wl(("  if(%s)then %s=%s(%s)or 0b0;end"):format(vM, vLINE, _tonumber, vM))
    wl("end")

    -- expected line is 2 because of the final packaging:
    -- line 1 = comment
    -- line 2 = return(function(...) ... end)(...)
    wl(("if(%s~=0b10)then"):format(vLINE))
    -- poison instead of hard error (optional â€“ change if you want hard fail)
    wl((" %s=function()end"):format(vEXEC))   -- kill the VM
    -- or keep the hard fail:
    -- wl(("  error(%s)"):format(encodeString("")))
    wl("end")
end

    local function emit_heavy_mt(tbl, mode, real_opt)
        mode = mode or "lock"
        local vMT    = R()
        local vTOSTR = R()
        local vLOCK  = R()
        local vREAL  = real_opt or R()
        local vRAWG  = R()

        wl(("local %s=function()return %s;end"):format(vTOSTR, encodeString("")))
        wl(("local %s={}"):format(vLOCK))

        if mode == "proxy" then
            wl(("local %s={}"):format(vREAL))
            wl(("for k,v in next,%s do %s[k]=v;%s[k]=nil;end"):format(tbl, vREAL, tbl))
        else
            if not real_opt then
                wl(("local %s=%s"):format(vREAL, tbl))
            end
        end

        wl(("local %s={}"):format(vMT))
        wl(("local %s=rawget"):format(vRAWG))
        wl(("%s[%s]=function(t,k)local v=%s(%s,k)if(v~=nil)then return v;end;end"):format(
            vMT, encodeString("__index"), vRAWG, vREAL))
        wl(("%s[%s]=function()end"):format(vMT, encodeString("__newindex")))
        wl(("%s[%s]=%s"):format(vMT, encodeString("__tostring"), vTOSTR))
        wl(("%s[%s]=%s"):format(vMT, encodeString("__metatable"), vLOCK))
        wl(("%s[%s]=function()return 0b0;end"):format(vMT, encodeString("__len")))
        wl(("setmetatable(%s,%s)"):format(tbl, vMT))

        if mode == "proxy" or real_opt then
            local vMT2 = R()
            wl(("local %s={[%s]=function(_,k)return rawget(%s,k)end,[%s]=function()end,[%s]=%s,[%s]=%s}"):format(
                vMT2,
                encodeString("__index"), vREAL,
                encodeString("__newindex"),
                encodeString("__tostring"), vTOSTR,
                encodeString("__metatable"), vLOCK))
            wl(("setmetatable(%s,%s)"):format(vREAL, vMT2))
        end
        return vREAL
    end

    local function b32xor(a,b) return ("%s[%s](%s,%s)"):format(_bit,encodeString("bxor"),a,b) end
    local function b32and(a,b) return ("%s[%s](%s,%s)"):format(_bit,encodeString("band"),a,b) end
    local function b32or(a,b)  return ("%s[%s](%s,%s)"):format(_bit,encodeString("bor"),a,b) end
    local function b32not(a)   return ("%s[%s](%s)"):format(_bit,encodeString("bnot"),a) end
    local function b32shl(a,b) return ("%s[%s](%s,%s)"):format(_bit,encodeString("lshift"),a,b) end
    local function b32shr(a,b) return ("%s[%s](%s,%s)"):format(_bit,encodeString("rshift"),a,b) end
    wl(("%s,%s,%s,%s,%s,%s,%s,%s,%s=tostring,tonumber,getfenv,bit32,math,string,table,type,typeof"):format(_tostring,_tonumber,_fenv,_bit,_math,_string,_table,_type,_of))
    do
        local vA = R() local vMap = R() local vS = R() local vG = R()
        local vVal = R() local vValB = R() local vI = R() local vC = R() local vD = R()
        local vC64 = R() local vC256 = R()
        wl(("local %s=%q"):format(vA, B64_ALPHA))
        wl(("local %s={}"):format(vMap))
        wl(("for %s=0b1,#%s do %s[%s:sub(%s,%s)]=%s-0b1;end"):format(vI, vA, vMap, vA, vI, vI, vI))
        wl(("local function %s(%s)"):format(POLY_B64_DEC, vS))
        wl(("  local %s={}"):format(vG))
        wl(("  local %s,%s=0b0,-0x08"):format(vVal, vValB))
        wl(("  for %s=0b1,#%s do"):format(vI, vS))
        wl(("    local %s=%s:sub(%s,%s)"):format(vC, vS, vI, vI))
        wl(("    local %s=%s[%s]"):format(vD, vMap, vC))
        wl(("    if(%s)then"):format(vD))
        wl(("      local %s=(0x40)"):format(vC64))
        wl(("      %s=%s*%s+%s"):format(vVal, vVal, vC64, vD))
        wl(("      %s=%s+0x06"):format(vValB, vValB))
        wl(("      if(%s>=0b0)then"):format(vValB))
        wl(("        local %s=(0x100)"):format(vC256))
        wl(("        %s[#%s+0b1]=%s['char'](%s['floor'](%s/0x02^%s)%%%s)"):format(vG, vG, _string, _math, vVal, vValB, vC256))
        wl(("        %s=%s%%(0x02^%s)"):format(vVal, vVal, vValB))
        wl(("        %s=%s-0x08"):format(vValB, vValB))
        wl("      end")
        wl("    end")
        wl("  end")
        wl(("  return %s['concat'](%s)"):format(_table, vG))
        wl("end")
    end
    dynamic()
    local man_c=R() local tbl_a=R() local seps=R()
    local man_r=R()
    wl(([[local function %s(%s,%s)
    %s=%s or ''
    if(#%s==0b0)then return '';end
    local %s=%s(%s[0x01])
    for i=0b10,#%s do
        %s=%s..%s..%s(%s[i])
    end
    return %s
end]]):format(man_c,tbl_a,seps,seps,seps,tbl_a,man_r,_tostring,tbl_a,tbl_a,man_r,man_r,seps,_tostring,tbl_a,man_r))
    do
        local CHUNK = 180
        local parts = {}
        for i = 1, #encoded, CHUNK do
            parts[#parts+1] = encoded:sub(i, i + CHUNK - 1)
        end
        local vARR = R()
        local vIDX = R()
        wl(("local %s={};local %s=0x0"):format(vARR, vIDX))
        for i = 1, #parts do
            local lit = string.format("%q", parts[i])
            wl(("%s=%s+0b1;%s[%s]=%s"):format(vIDX, vIDX, vARR, vIDX, lit))
        end
        wl(("local %s=%s(%s)"):format(vPAYLOAD, man_c, vARR))
    end
    wl(("local %s=%d"):format(vKEY,cipher_key))
    emit_junk()
    do
        local vS=R() local vOUT=R() local vI=R() local vC=R()
        local vVAL=R() local vCOUNT=R() local vB=R() local vJ=R()
        local vTMP1=R() local vTMP2=R() local vTMP3=R() local vTMP4=R()
        local vN33=R() local vN85=R() local vN5=R() local vN256=R()
        local vN84=R() local vN24=R() local vCHZ=R()
        local vCHSP=R() local vCHNL=R()
        local vCHCR=R() local vCHTB=R()
        wl(("local %s=(99-66)"):format(vN33))
        wl(("local %s=(170-85)"):format(vN85))
        wl(("local %s=(12-7)"):format(vN5))
        wl(("local %s=(512-256)"):format(vN256))
        wl(("local %s=(168-84)"):format(vN84))
        wl(("local %s=(48-24)"):format(vN24))
        wl(("local %s=%s"):format(vCHZ, encodeString("z")))
        wl(("local %s=%s"):format(vCHSP, encodeString(" ")))
        wl(("local %s=%s"):format(vCHNL, encodeString("\n")))
        wl(("local %s=%s"):format(vCHCR, encodeString("\r")))
        wl(("local %s=%s"):format(vCHTB, encodeString("\t")))
        wl(("local function %s(%s)"):format(vDEA85,vS))
        wl(("  local %s={}"):format(vOUT))
        wl(("  local %s,%s=0b0,0b0"):format(vVAL,vCOUNT))
        wl(("  for %s=0b1,#%s do"):format(vI,vS))
        wl(("    local %s=%s[%s](%s,%s,%s)"):format(vC,vS,encodeString("sub"),vS,vI,vI))
        wl(("    if(%s~=%s and %s~=%s and %s~=%s and %s~=%s)then"):format(
            vC,vCHSP, vC,vCHNL, vC,vCHCR, vC,vCHTB))
        wl(("      if(%s==%s and %s==0b0)then"):format(vC,vCHZ,vCOUNT))
        wl(("        %s[#%s+0b1]=%s[%s](0b0);%s[#%s+0b1]=%s[%s](0b0);%s[#%s+0b1]=%s[%s](0b0);%s[#%s+0b1]=%s[%s](0b0)"):format(
            vOUT,vOUT,_string,encodeString("char"), vOUT,vOUT,_string,encodeString("char"),
            vOUT,vOUT,_string,encodeString("char"), vOUT,vOUT,_string,encodeString("char")))
        wl(("      else"))
        wl(("        local %s=%s[%s](%s)-%s"):format(vB,_string,encodeString("byte"),vC,vN33))
        wl(("        if(%s>=0b0 and %s<%s)then"):format(vB,vB,vN85))
        wl(("          %s=%s*%s+%s;%s=%s+0b1"):format(vVAL,vVAL,vN85,vB,vCOUNT,vCOUNT))
        wl(("          if(%s==%s)then"):format(vCOUNT,vN5))
        wl(("            local %s=(0b10^2_4);local %s=(0x2^1_6)"):format(vTMP1,vTMP2))
        wl(("            %s[#%s+0b1]=%s[%s](%s[%s](%s/%s,%s))"):format(vOUT,vOUT,_string,encodeString("char"),_math,encodeString("fmod"),vVAL,vTMP1,vN256))
        wl(("            %s[#%s+0b1]=%s[%s](%s[%s](%s/%s,%s))"):format(vOUT,vOUT,_string,encodeString("char"),_math,encodeString("fmod"),vVAL,vTMP2,vN256))
        wl(("            %s[#%s+0b1]=%s[%s](%s[%s](%s/%s,%s))"):format(vOUT,vOUT,_string,encodeString("char"),_math,encodeString("fmod"),vVAL,vN256,vN256))
        wl(("            %s[#%s+0b1]=%s[%s](%s[%s](%s,%s))"):format(vOUT,vOUT,_string,encodeString("char"),_math,encodeString("fmod"),vVAL,vN256))
        wl(("            %s,%s=0b0,0b0"):format(vVAL,vCOUNT))
        wl("          end")
        wl("        end")
        wl("      end")
        wl("    end")
        wl("  end")
        wl(("  if(%s>0b0)then"):format(vCOUNT))
        wl(("    for %s=%s+0b1,%s do %s=%s*%s+%s;end"):format(vJ,vCOUNT,vN5,vVAL,vVAL,vN85,vN84))
        wl(("    local %s=%s-0b1"):format(vCOUNT,vCOUNT))
        wl(("    local %s=(0b10^2_4);local %s=(0x02^1_6)"):format(vTMP3,vTMP4))
        wl(("    if(%s>=0b1)then %s[#%s+0b1]=%s[%s](%s[%s](%s/%s,%s))end"):format(vCOUNT,vOUT,vOUT,_string,encodeString("char"),_math,encodeString("fmod"),vVAL,vTMP3,vN256))
        wl(("    if(%s>=0b10)then %s[#%s+0b1]=%s[%s](%s[%s](%s/%s,%s))end"):format(vCOUNT,vOUT,vOUT,_string,encodeString("char"),_math,encodeString("fmod"),vVAL,vTMP4,vN256))
        wl(("    if(%s>=0B11)then %s[#%s+0b1]=%s[%s](%s[%s](%s/%s,%s))end"):format(vCOUNT,vOUT,vOUT,_string,encodeString("char"),_math,encodeString("fmod"),vVAL,vN256,vN256))
        wl(("    if(%s>=(2+2))then %s[#%s+0b1]=%s[%s](%s[%s](%s,%s))end"):format(vCOUNT,vOUT,vOUT,_string,encodeString("char"),_math,encodeString("fmod"),vVAL,vN256))
        wl("  end")
        wl(("  return %s(%s)"):format(man_c,vOUT))
        wl("end")
    end
    do
        local vBLOB=R() local vOUT=R() local vPOS=R() local vLEN=R() local vI=R() local vN=R() local vB=R()
        local vB1=R() local vB2=R() local vB3=R() local vB4=R()
        wl(("local function %s(%s)"):format(vUNPACK, vBLOB))
        wl(("  local function %s(%s)"):format(vN, vPOS))
        wl(("    local %s,%s,%s,%s=%s[%s](%s,%s,%s+0b11)"):format(vB1,vB2,vB3,vB4,vBLOB,encodeString("byte"),vBLOB,vPOS,vPOS))
        wl(("    return %s+%s*25_6+%s*65536+%s*16__77_72_16"):format(vB1,vB2,vB3,vB4))
        wl("  end")
        wl(("  local %s=%s(0x01)"):format(vLEN, vN))
        wl(("  local %s={}"):format(vOUT))
        wl(("  local %s=0x0"):format(vI))
        wl(("  local %s=0x05"):format(vPOS))
        wl(("  while %s<%s do"):format(vI, vLEN))
        wl(("    local %s=%s(%s)"):format(vB, vN, vPOS))
        wl(("    %s=%s+0x04"):format(vPOS, vPOS))
        wl(("    %s=%s+0b1;if(0x02-0b0==0B10)then %s[%s]=%s[%s](%s[%s](%s,0xFF));if(%s>=%s)then break end end"):format(vI,vI,vOUT,vI,_string,encodeString("char"),_bit,encodeString("band"),vB,vI,vLEN))
        wl(("    %s=%s+0b1;%s[%s]=%s[%s](%s[%s](%s[%s](%s,0x8),0xFF));if(%s>=%s)then break end"):format(vI,vI,vOUT,vI,_string,encodeString("char"),_bit,encodeString("band"),_bit,encodeString("rshift"),vB,vI,vLEN))
        wl(("    %s=%s+0b1;%s[%s]=%s[%s](%s[%s](%s[%s](%s,16),0xFF));if(%s>=%s)then break end"):format(vI,vI,vOUT,vI,_string,encodeString("char"),_bit,encodeString("band"),_bit,encodeString("rshift"),vB,vI,vLEN))
        wl(("    %s=%s+0b1;%s[%s]=%s[%s](%s[%s](%s[%s](%s,24),0xFF));if(%s>=%s)then break end"):format(vI,vI,vOUT,vI,_string,encodeString("char"),_bit,encodeString("band"),_bit,encodeString("rshift"),vB,vI,vLEN))
        wl("  end")
        wl(("  return %s(%s)"):format(man_c, vOUT))
        wl("end")
    end
    emit_junk()
    emit_dead_end()
    do
        local vS=R() local vH=R() local vBUF=R() local vI=R()
        local vJ=R() local vCIN=R() local vXB=R()
        wl(("local function %s(%s,%s)"):format(vXORDEC,vS,vH))
        wl(("  local %s=if(1856-1478==3_68)then 0B1 else {}"):format(vBUF))
        wl(("  for %s=0b1,#%s do"):format(vI,vS))
        local _n256=R()
        wl(("local %s=(1484-1228)or(1481-1228)"):format(_n256))
        wl(("    local %s=if(typeof(%s)~=%s)then %s[%s](%s,%s)else nil"):format(vJ,vBUF,encodeString("number"),_math,encodeString("fmod"),vH,_n256))
        wl(("    local %s=%s[%s](%s,%s)"):format(vCIN,vS,encodeString("byte"),vS,vI))
        wl(("    local %s=%s"):format(vXB,b32xor(vCIN,vJ)))
        wl(("    %s[%s]=%s[%s](%s)"):format(vBUF,vI,_string,encodeString("char"),vXB))
        wl(("    %s=%s[%s](%s*131+%s+0b1,4294967296)"):format(vH,_math,encodeString("fmod"),vH,vCIN))
        wl("  end")
        wl(("  return %s(%s)"):format(man_c,vBUF))
        wl("end")
    end
    anti_line_check()
    do
        local vS=R() local vOUT=R() local vPOS=R() local vLEN=R()
        local vRVI=R() local vBR=R() local vACC=R() local vSH=R()
        local vFLAG=R() local vBIT=R() local vOFF=R() local vCNT=R()
        local vSRC=R() local vK=R()
        wl(("local function %s(%s)"):format(vLZDEC,vS))
        wl(("  local %s={}"):format(vOUT))
        wl(("  local %s=0x01"):format(vPOS))
        wl(("  local %s=#%s"):format(vLEN,vS))
        wl(("  local function %s()"):format(vRVI))
        wl(("    local %s,%s=0b0,0b0"):format(vACC,vSH))
        wl("    while true do")
        wl(("      local %s=%s[%s](%s,%s);%s=%s+0x01"):format(vBR,vS,encodeString("byte"),vS,vPOS,vPOS,vPOS))
        wl(("      %s=%s+%s[%s](%s,128)*0x02^%s"):format(vACC,vACC,_math,encodeString("fmod"),vBR,vSH))
        wl(("      if(%s<1_28)then break;end"):format(vBR))
        wl(("      %s=%s+0x07"):format(vSH,vSH))
        wl("    end")
        wl(("    return %s"):format(vACC))
        wl("  end")
        wl(("  while %s<=%s do"):format(vPOS,vLEN))
        wl(("    local %s=%s[%s](%s,%s);%s=%s+0b1"):format(vFLAG,vS,encodeString("byte"),vS,vPOS,vPOS,vPOS))
        wl(("    for %s=0,0x07 do"):format(vBIT))
        wl(("      if(%s>%s)then break;end"):format(vPOS,vLEN))
        wl(("      if(%s~=0x0)then"):format(b32and(b32shr(vFLAG,vBIT),"1")))
        wl(("        local %s=%s()"):format(vOFF,vRVI))
        wl(("        local %s=%s()"):format(vCNT,vRVI))
        wl(("        local %s=#%s-%s+0b1"):format(vSRC,vOUT,vOFF))
        wl(("        for %s=0x0,%s-0B1 do %s[#%s+0B1]=%s[%s+%s];end"):format(vK,vCNT,vOUT,vOUT,vOUT,vSRC,vK))
        wl("      else")
        wl(("        %s[#%s+0x01]=%s[%s](%s[%s](%s,%s));%s=%s+0b1"):format(vOUT,vOUT,_string,encodeString("char"),vS,encodeString("byte"),vS,vPOS,vPOS,vPOS))
        wl("      end") wl("    end") wl("  end")
        wl(("  return %s(%s)"):format(man_c,vOUT))
        wl("end")
    end
    do
        local vS=R() local vP=R() local vACC=R() local vSH=R() local vB=R()
        wl(("local function %s(%s,%s)"):format(vRV,vS,vP))
        wl(("  local %s,%s=0x0,0b0"):format(vACC,vSH))
        wl("  while true do")
        wl(("    local %s=%s[%s](%s,%s);%s=%s+0b1"):format(vB,vS,encodeString("byte"),vS,vP,vP,vP))
        wl(("    %s=%s+%s[%s](%s,128)*0B10^%s"):format(vACC,vACC,_math,encodeString("fmod"),vB,vSH))
        wl(("    if(%s<1_28)then break;end"):format(vB))
        wl(("    %s=%s+0x07"):format(vSH,vSH))
        wl("  end")
        wl(("  return %s,%s"):format(vACC,vP))
        wl("end")
        local vZ=R() local vS2=R() local vP2=R()
        wl(("local function %s(%s,%s)"):format(vRS,vS2,vP2))
        wl(("  local %s;%s,%s=%s(%s,%s)"):format(vZ,vZ,vP2,vRV,vS2,vP2))
        wl(("  if(%s[%s](%s,0x02)==0b0)then return %s/0x02,%s else return -(%s+0b1)/0b10,%s;end"):format(_math,encodeString("fmod"),vZ,vZ,vP2,vZ,vP2))
        wl("end")
    end
    -- anti_line_check() call removed (Bug Fix 4)
    do
        local vS=R() local vP=R() local vG=R() local vCNT=R()
        local vI=R() local vTYP=R() local vLEN=R() local vVAL=R()
        wl(("local %s=%d"):format(vCONSTKEY,const_key))
        wl(("local %s=%d"):format(vOPKEY,op_key))
        wl(("local %s=%d"):format(vINTKEY,integrity_key))
        do
            local vSTR=R() local vIDX=R() local vBUF=R()
            local vSTATE=R() local vKEYSTR=R() local vI=R() local vJ=R()
            local vS=R() local vT=R() local vPADLEN=R() local vB=R()
            local vMIX=R() local vKLEN=R()
            wl(("local function %s(%s,%s)"):format(vCDECRYPT,vSTR,vIDX))
            wl(("  local function %s(%s)"):format(vMIX,vSTATE))
            wl(("    %s=%s[%s](%s,4294967296)"):format(vSTATE,_math,encodeString("fmod"),vSTATE))
            wl(("    %s=%s[%s](%s,%s[%s](%s,16))"):format(vSTATE,_bit,encodeString("bxor"),vSTATE,_bit,encodeString("rshift"),vSTATE))
            wl(("    %s=%s[%s](%s+0x7ed55d16,4294967296)"):format(vSTATE,_math,encodeString("fmod"),vSTATE))
            wl(("    %s=%s[%s](%s,%s[%s](%s,13))"):format(vSTATE,_bit,encodeString("bxor"),vSTATE,_bit,encodeString("rshift"),vSTATE))
            wl(("    %s=%s[%s](%s+0xc761c23c,4294967296)"):format(vSTATE,_math,encodeString("fmod"),vSTATE))
            wl(("    %s=%s[%s](%s,%s[%s](%s,16))"):format(vSTATE,_bit,encodeString("bxor"),vSTATE,_bit,encodeString("rshift"),vSTATE))
            wl(("    %s=%s[%s](%s+0x165667b1,4294967296)"):format(vSTATE,_math,encodeString("fmod"),vSTATE))
            wl(("    return %s"):format(vSTATE))
            wl("  end")
            wl(("  local %s=%s(%s[%s](%s+%s*2654435761+0x9e3779b9,4294967296))"):format(vSTATE,vMIX,_math,encodeString("fmod"),vCONSTKEY,vIDX))
            wl(("  local %s={}"):format(vKEYSTR))
            wl(("  for %s=0b1,0x10 do"):format(vI))
            wl(("    %s=%s(%s[%s](%s+%s*0x9e3779b9,4294967296))"):format(vSTATE,vMIX,_math,encodeString("fmod"),vSTATE,vI))
            wl(("    %s[%s]=%s[%s](%s[%s](%s,256))"):format(vKEYSTR,vI,_string,encodeString("char"),_math,encodeString("fmod"),vSTATE))
            wl("  end")
            wl(("  %s=%s(%s)"):format(vKEYSTR,man_c,vKEYSTR))
            wl(("  local %s={}"):format(vS))
            wl(("  for %s=0b0,255 do %s[%s]=%s end"):format(vI,vS,vI,vI))
            wl(("  local %s=0b0"):format(vJ))
            wl(("  local %s=#%s"):format(vKLEN,vKEYSTR))
            wl(("  for %s=0b0,255 do"):format(vI))
            wl(("    %s=%s[%s](%s+%s[%s]+%s[%s](%s,%s[%s](%s,%s)+0b1),256)"):format(vJ,_math,encodeString("fmod"),vJ,vS,vI,vKEYSTR,encodeString("byte"),vKEYSTR,_math,encodeString("fmod"),vI,vKLEN))
            wl(("    %s[%s],%s[%s]=%s[%s],%s[%s]"):format(vS,vI,vS,vJ,vS,vJ,vS,vI))
            wl("  end")
            wl(("  local %s,%s=0b0,0b0"):format(vI,vJ))
            wl(("  local %s={}"):format(vBUF))
            wl(("  for %s=0b1,#%s do"):format(vT,vSTR))
            wl(("    %s=%s[%s](%s+0b1,256)"):format(vI,_math,encodeString("fmod"),vI))
            wl(("    %s=%s[%s](%s+%s[%s],256)"):format(vJ,_math,encodeString("fmod"),vJ,vS,vI))
            wl(("    %s[%s],%s[%s]=%s[%s],%s[%s]"):format(vS,vI,vS,vJ,vS,vJ,vS,vI))
            wl(("    local %s=%s[%s](%s[%s]+%s[%s],256)"):format(vB,_math,encodeString("fmod"),vS,vI,vS,vJ))
            wl(("    %s[%s]=%s[%s](%s[%s](%s[%s](%s,%s),%s[%s]))"):format(vBUF,vT,_string,encodeString("char"),_bit,encodeString("bxor"),vSTR,encodeString("byte"),vSTR,vT,vS,vB))
            wl("  end")
            wl(("  local %s=%s(%s)"):format(vSTR,man_c,vBUF))
            wl(("  local %s=%s[%s](%s,0b1)"):format(vPADLEN,vSTR,encodeString("byte"),vSTR))
            wl(("  return %s[%s](%s,%s+0x02)"):format(vSTR,encodeString("sub"),vSTR,vPADLEN))
            wl("end")
        end
        local vS=R() local vP=R() local vG=R() local vCNT=R()
        local vI=R() local vTYP=R() local vLEN=R() local vVAL=R()
        local vTYPES=R()
        wl(("local function %s(%s,%s)"):format(vRCONSTS,vS,vP))
        wl(("  local %s={}"):format(vG))
        wl(("  local %s={}"):format(vTYPES))
        wl(("  local %s;%s,%s=%s(%s,%s)"):format(vCNT,vCNT,vP,vRV,vS,vP))
        wl(("  for %s=0b0,%s-0x01 do"):format(vI,vCNT))
        wl(("    local %s=%s[%s](%s,%s);%s=%s+0b1"):format(vTYP,vS,encodeString("byte"),vS,vP,vP,vP))
        wl(("    local %s;%s,%s=%s(%s,%s)"):format(vLEN,vLEN,vP,vRV,vS,vP))
        wl(("    local %s=%s[%s](%s,%s,%s+%s-0x01);%s=%s+%s"):format(vVAL,vS,encodeString("sub"),vS,vP,vP,vLEN,vP,vP,vLEN))
        wl(("    %s[%s]=%s"):format(vG,vI,vVAL))
        wl(("    %s[%s]=%s"):format(vTYPES,vI,vTYP))
        wl("  end")
        wl(("  return %s,%s,%s"):format(vG,vP,vTYPES))
        wl("end")
    end
    dynamic()
    do
        local fmt_entries={}
        for i=0,NUM_OPCODES-1 do
            local f = OPCODES[i].fmt
            fmt_entries[#fmt_entries+1]=("[%d]=%s"):format(op_perm[i], (#f > 0 and encodeString(f) or '""'))
        end
        wl(("local %s={%s}"):format(vFMT,table.concat(fmt_entries,",")))
        emit_heavy_mt(vFMT, "lock")
    end
    do
        local vS=R() local vP=R() local vG=R() local vCNT=R()
        local vI=R() local vOP=R() local vF=R() local vFI=R()
        local vCH=R() local vVAL=R()
        local vA=R() local vB=R() local vC=R() local vD=R()
        local vK=R()
        wl(("local function %s(%s,%s)"):format(vRCODE,vS,vP))
        wl(("  local %s={}"):format(vG))
        wl(("  local %s;%s,%s=%s(%s,%s)"):format(vCNT,vCNT,vP,vRV,vS,vP))
        wl(("  for %s=0b1,%s do"):format(vI,vCNT))
        wl(("    local %s=%s[%s](%s,%s);%s=%s+0x1"):format(vOP,vS,encodeString("byte"),vS,vP,vP,vP))
        wl(("    local %s=%s[%s]((%s*2654435761+%s),256)"):format(vK,_math,encodeString("fmod"),vI,vOPKEY))
        wl(("    %s=%s[%s](%s,%s)"):format(vOP,_bit,encodeString("bxor"),vOP,vK))
        wl(("    local %s=%s[%s] or ''"):format(vF,vFMT,vOP))
        wl(("    local %s,%s,%s,%s=0x0,0b0,0x0,0B0"):format(vA,vB,vC,vD))
        wl(("    for %s=0b1,#%s do"):format(vFI,vF))
        wl(("      local %s=%s[%s](%s,%s,%s)"):format(vCH,vF,encodeString("sub"),vF,vFI,vFI))
        wl(("      local %s"):format(vVAL))
        wl(("      if(%s=='s')then %s,%s=%s(%s,%s)else %s,%s=%s(%s,%s)end"):format(vCH,vVAL,vP,vRS,vS,vP,vVAL,vP,vRV,vS,vP))
        wl(("      if(%s==0b1)then %s=%s elseif %s==0b10 then %s=%s elseif %s==0B11 then %s=%s else %s=%s;end"):format(vFI,vA,vVAL,vFI,vB,vVAL,vFI,vC,vVAL,vD,vVAL))
        wl("    end")
        wl(("    %s[%s]={%s=%s,a=%s,b=%s,c=%s,d=%s}"):format(vG,vI,fOP,vOP,vA,vB,vC,vD))
        wl("  end")
        wl(("  return %s,%s"):format(vG,vP))
        wl("end")
    end
    do
        local vS=R() local vP=R() local vG=R() local vCNT=R() local vI=R()
        local vPAR=R() local vVA=R() local vINS=R()
        local vUVC=R() local vUVI=R() local vUVS=R() local vINST=R() local vIDX=R()
        wl(("local function %s(%s,%s)"):format(vRPROTOS,vS,vP))
        wl(("  local %s={}"):format(vG))
        wl(("  local %s;%s,%s=%s(%s,%s)"):format(vCNT,vCNT,vP,vRV,vS,vP))
        wl(("  for %s=0x0,%s-0B1 do"):format(vI,vCNT))
        wl(("    local %s=%s[%s](%s,%s);%s=%s+0x1"):format(vPAR,vS,encodeString("byte"),vS,vP,vP,vP))
        wl(("    local %s=%s[%s](%s,%s);%s=%s+0b1"):format(vVA,vS,encodeString("byte"),vS,vP,vP,vP))
        wl(("    local %s;%s,%s=%s(%s,%s)"):format(vUVC,vUVC,vP,vRV,vS,vP))
        wl(("    local %s={}"):format(vUVS))
        wl(("    for %s=0x1,%s do"):format(vUVI,vUVC))
        wl(("      local %s=%s[%s](%s,%s);%s=%s+0b1"):format(vINST,vS,encodeString("byte"),vS,vP,vP,vP))
        wl(("      local %s;%s,%s=%s(%s,%s)"):format(vIDX,vIDX,vP,vRV,vS,vP))
        wl(("      %s[%s]={[%s]=(%s==0b1),[%s]=%s}"):format(vUVS,vUVI,encodeString(fINSTACK),vINST,encodeString(fIDX),vIDX))
        wl("    end")
        wl(("    local %s;%s,%s=%s(%s,%s)"):format(vINS,vINS,vP,vRCODE,vS,vP))
        wl(("    %s[%s]={%s=%s,%s=(%s==0b1),%s=%s,%s=%s}"):format(vG,vI,fPARAMS,vPAR,fISVARARG,vVA,fINSTRUCTIONS,vINS,fUPVALUES,vUVS))
        wl("  end")
        wl(("  return %s,%s"):format(vG,vP))
        wl("end")
    end
    do
        emit_junk()
        emit_junk()
        local dnz=R()local vxn=R()local dwe=R()
        wl(("local %s;local %s,%s=pcall(function()loadstring(0b11)()end)if(%s or not %s)then %s={};else %s=0b10;end;%s*=0x01"):format(dwe,dnz,vxn,dnz,vxn,dwe,dwe,dwe))
    end
    do
        local vRK=R() local vREGS=R() local vCON=R()
        wl(("local function %s(%s,%s,%s)"):format(vNAVIBO,vRK,vREGS,vCON))
        wl(("  if(%s[%s](%s,0x2)==0b1)then return %s(%s[%s]((%s-0b1)/0B10)) else return %s[%s[%s](%s/0b10)];end"):format(_math,encodeString("fmod"),vRK,vCON,_math,encodeString("floor"),vRK,vREGS,_math,encodeString("floor"),vRK))
        wl("end")
    end
    wl(("local %s"):format(vEXEC))
    do
        local vPROTO=R() local vENV2=R() local vCON2=R() local vPROTOS2=R() local vINNER=R()
        local vPARENTUV=R() local vPARENTREGS=R() local vUVLIST=R() local vUI=R() local vDESC=R() local vD=R()
        wl(("local function %s(%s,%s,%s,%s,%s,%s)"):format(vNEWCLOS,vPROTO,vENV2,vCON2,vPROTOS2,vPARENTUV,vPARENTREGS))
        wl(("  local %s={}"):format(vUVLIST))
        wl(("  local %s=%s.%s or {}"):format(vDESC,vPROTO,fUPVALUES))
        wl(("  for %s=0b1,#%s do"):format(vUI,vDESC))
        wl(("    local %s=%s[%s]"):format(vD,vDESC,vUI))
        wl(("    if(%s[%s])then %s[%s]={[%s]=%s[%s[%s]],[%s]=true,[%s]=%s[%s],[%s]=%s} else %s[%s]=%s[%s[%s]];end"):format(
            vD,encodeString(fINSTACK), vUVLIST,vUI, encodeString(fCELLV),vPARENTREGS,vD,encodeString(fIDX), encodeString(fOPEN), encodeString(fREG),vD,encodeString(fIDX), encodeString(fREGS),vPARENTREGS,
            vUVLIST,vUI, vPARENTUV,vD,encodeString(fIDX)))
        wl("  end")
        wl("  return function(...)")
        wl(("    local %s={...}"):format(vINNER))
        wl(("    return %s(%s.%s,%s,%s,%s,%s,%s,%s.%s or 0)"):format(
            vEXEC,vPROTO,fINSTRUCTIONS,vCON2,vENV2,vPROTOS2,vINNER,vUVLIST,vPROTO,fPARAMS))
        wl("  end")
        wl("end")
    end
    local function ha() return ("%s,%s,%s,%s,%s,%s"):format(aINS,aREGS,aCONSTS,aENV,aFRM,aPROTOS) end
    local function nv(field) return ("%s(%s.%s,%s,%s)"):format(vNAVIBO,aINS,field,aREGS,aCONSTS) end
    wl(("local function %s(%s) %s[%s.a]=%s(%s.b);end"):format(H[0],ha(),aREGS,aINS,aCONSTS,aINS))
    wl(("local function %s(%s) %s[%s.a]=(%s.b==0b1)end"):format(H[1],ha(),aREGS,aINS,aINS))
    wl(("local function %s(%s) %s[%s.a]=nil;end"):format(H[2],ha(),aREGS,aINS))
    wl(("local function %s(%s) %s[%s.a]=%s[%s.b];end"):format(H[3],ha(),aREGS,aINS,aREGS,aINS))
    wl(("local function %s(%s) %s[%s.a]=%s[%s(%s.b)];end"):format(H[4],ha(),aREGS,aINS,aENV,aCONSTS,aINS))
    wl(("local function %s(%s) %s[%s(%s.a)]=%s[%s.b];end"):format(H[5],ha(),aENV,aCONSTS,aINS,aREGS,aINS))
    wl(("local function %s(%s) %s[%s.a]=%s[%s.b][%s[%s.c]];end"):format(H[6],ha(),aREGS,aINS,aREGS,aINS,aREGS,aINS))
    wl(("local function %s(%s) %s[%s.a][%s[%s.b]]=%s[%s.c];end"):format(H[7],ha(),aREGS,aINS,aREGS,aINS,aREGS,aINS))
    wl(("local function %s(%s) %s[%s.a]={};end"):format(H[8],ha(),aREGS,aINS))
    do
        local vADD = R()
        local styles = {
            ("local %s=function(a,b)local r,t=a,b;r+=0b1;t-=0x01;if(r and t)then r-=0B1;t+=0x01;return r+t;end;return a,b;end"):format(vADD),
        }
        wl(styles[math.random(1, #styles)])
        emit_junk()
        wl(("local function %s(%s) %s[%s.a]=%s(%s,%s);end"):format(H[9],ha(),aREGS,aINS,vADD,nv("b"),nv("c")))
    end
    do
        local vSUB=R() local vMUL=R() local vDIV=R() local vMOD=R() local vPOW=R()
        local vNEG=R() local vLEN=R()
        local vBAND=R() local vBOR=R() local vBXOR=R() local vBNOT=R()
        local vBSHL=R() local vBSHR=R()
        local vCONCAT=R()
        local vEQ=R() local vNEQ=R() local vLT=R() local vLE=R()
        local vGT=R() local vGE=R() local vNOT=R()

        local sub_styles   = {("local %s=function(a,b)local r,t=a,b;r-=0b1;t+=0x01;if(r and t)then r+=0B1;t-=0x01;return r-t;end;return a,b;end"):format(vSUB)}
        local mul_styles   = {("local %s=function(a,b)return a*b end"):format(vMUL),("local %s=function(a,b)local c=a*b;return c end"):format(vMUL),("local %s=function(a,b)local c=a;return c*b end"):format(vMUL),("local %s=function(a,b)return (a)*(b) end"):format(vMUL)}
        local div_styles   = {("local %s=function(a,b)return a/b end"):format(vDIV),("local %s=function(a,b)local c=a/b;return c end"):format(vDIV),("local %s=function(a,b)local c=a;return c/b end"):format(vDIV)}
        local mod_styles   = {("local %s=function(a,b)return a%%b end"):format(vMOD),("local %s=function(a,b)local c=a%%b;return c end"):format(vMOD),("local %s=function(a,b)local c=a;return c%%b end"):format(vMOD)}
        local pow_styles   = {("local %s=function(a,b)return a^b end"):format(vPOW),("local %s=function(a,b)local c=a^b;return c end"):format(vPOW),("local %s=function(a,b)local c=a;return c^b end"):format(vPOW)}
        local neg_styles   = {("local %s=function(a)return -a end"):format(vNEG),("local %s=function(a)local c=-a;return c end"):format(vNEG)}
        local len_styles   = {("local %s=function(a)return #a end"):format(vLEN),("local %s=function(a)local c=#a;return c end"):format(vLEN)}
        local band_styles  = {("local %s=function(a,b)return %s[%s](a,b)end"):format(vBAND,_bit,encodeString("band")),("local %s=function(a,b)local c=%s[%s](a,b);return c end"):format(vBAND,_bit,encodeString("band"))}
        local bor_styles   = {("local %s=function(a,b)return %s[%s](a,b)end"):format(vBOR,_bit,encodeString("bor")),("local %s=function(a,b)local c=%s[%s](a,b);return c end"):format(vBOR,_bit,encodeString("bor"))}
        local bxor_styles  = {("local %s=function(a,b)return %s[%s](a,b)end"):format(vBXOR,_bit,encodeString("bxor")),("local %s=function(a,b)local c=%s[%s](a,b);return c end"):format(vBXOR,_bit,encodeString("bxor"))}
        local bnot_styles  = {("local %s=function(a)return %s[%s](a)end"):format(vBNOT,_bit,encodeString("bnot")),("local %s=function(a)local c=%s[%s](a);return c end"):format(vBNOT,_bit,encodeString("bnot"))}
        local bshl_styles  = {("local %s=function(a,b)return %s[%s](a,b)end"):format(vBSHL,_bit,encodeString("lshift")),("local %s=function(a,b)local c=%s[%s](a,b);return c end"):format(vBSHL,_bit,encodeString("lshift"))}
        local bshr_styles  = {("local %s=function(a,b)return %s[%s](a,b)end"):format(vBSHR,_bit,encodeString("rshift")),("local %s=function(a,b)local c=%s[%s](a,b);return c end"):format(vBSHR,_bit,encodeString("rshift"))}
        local concat_styles= {("local %s=function(a,b)if(0x01==0b10)then return a and b;end;return %s(a)..%s(b)end"):format(vCONCAT,_tostring,_tostring)}
        local eq_styles    = {("local %s=function(a,b)if(0b10>0x03)then return a<b;end;return a==b end"):format(vEQ)}
        local neq_styles   = {("local %s=function(a,b)if(0x03-0b10==0b0)then return a>b;end;return a~=b end"):format(vNEQ)}
        local lt_styles    = {("local %s=function(a,b)if(0b11-0b1==0x01)then return a==b;end;return a<b end"):format(vLT)}
        local le_styles    = {("local %s=function(a,b)if(0x01-0b10==0b11)then return a~=b;end;return a<=b end"):format(vLE)}
        local gt_styles    = {("local %s=function(a,b)if(b==qw)then return b;end;return a>b end"):format(vGT)}
        local ge_styles    = {("local %s=function(a,b)if(a==yu)then return 0b1>0b0;end;return a>=b;end"):format(vGE)}
        local not_styles   = {("local %s=function(a)if(0x02>0b11)then return a==0b1;end;return not a;end"):format(vNOT)}

        local function pick(t) return t[math.random(1,#t)] end

        wl(pick(sub_styles))    wl(pick(mul_styles))    wl(pick(div_styles))
        emit_junk()
        wl(pick(mod_styles))    wl(pick(pow_styles))    wl(pick(neg_styles))
        wl(pick(len_styles))    wl(pick(band_styles))   wl(pick(bor_styles))
        emit_junk()
        wl(pick(bxor_styles))   wl(pick(bnot_styles))   wl(pick(bshl_styles))
        wl(pick(bshr_styles))   wl(pick(concat_styles)) wl(pick(eq_styles))
        wl(pick(neq_styles))    wl(pick(lt_styles))     wl(pick(le_styles))
        emit_junk()
        wl(pick(gt_styles))     wl(pick(ge_styles))     wl(pick(not_styles))
        emit_junk()

        wl(("local function %s(%s) %s[%s.a]=%s(%s,%s);end"):format(H[10],ha(),aREGS,aINS,vSUB,nv("b"),nv("c")))
        emit_junk()
        wl(("local function %s(%s) %s[%s.a]=%s(%s,%s);end"):format(H[11],ha(),aREGS,aINS,vMUL,nv("b"),nv("c")))
        wl(("local function %s(%s) %s[%s.a]=%s(%s,%s);end"):format(H[12],ha(),aREGS,aINS,vDIV,nv("b"),nv("c")))
        wl(("local function %s(%s) %s[%s.a]=%s(%s,%s);end"):format(H[13],ha(),aREGS,aINS,vMOD,nv("b"),nv("c")))
        emit_junk()
        wl(("local function %s(%s) %s[%s.a]=%s(%s,%s);end"):format(H[14],ha(),aREGS,aINS,vPOW,nv("b"),nv("c")))
        wl(("local function %s(%s) %s[%s.a]=%s(%s);end"):format(H[15],ha(),aREGS,aINS,vNEG,nv("b")))
        emit_dead_end()
        wl(("local function %s(%s) %s[%s.a]=%s(%s);end"):format(H[16],ha(),aREGS,aINS,vLEN,nv("b")))
        wl(("local function %s(%s) %s[%s.a]=%s(%s,%s);end"):format(H[17],ha(),aREGS,aINS,vBAND,nv("b"),nv("c")))
        emit_dead_end()
        wl(("local function %s(%s) %s[%s.a]=%s(%s,%s);end"):format(H[18],ha(),aREGS,aINS,vBOR,nv("b"),nv("c")))
        wl(("local function %s(%s) %s[%s.a]=%s(%s,%s);end"):format(H[19],ha(),aREGS,aINS,vBXOR,nv("b"),nv("c")))
        emit_junk()
        wl(("local function %s(%s) %s[%s.a]=%s(%s);end"):format(H[20],ha(),aREGS,aINS,vBNOT,nv("b")))
        wl(("local function %s(%s) %s[%s.a]=%s(%s,%s);end"):format(H[21],ha(),aREGS,aINS,vBSHL,nv("b"),nv("c")))
        emit_dead_end()
        wl(("local function %s(%s) %s[%s.a]=%s(%s,%s);end"):format(H[22],ha(),aREGS,aINS,vBSHR,nv("b"),nv("c")))
        wl(("local function %s(%s) %s[%s.a]=%s(%s,%s);end"):format(H[23],ha(),aREGS,aINS,vCONCAT,nv("b"),nv("c")))
        emit_dead_end()
        wl(("local function %s(%s) %s[%s.a]=%s(%s,%s);end"):format(H[24],ha(),aREGS,aINS,vEQ,nv("b"),nv("c")))
        wl(("local function %s(%s) %s[%s.a]=%s(%s,%s);end"):format(H[25],ha(),aREGS,aINS,vNEQ,nv("b"),nv("c")))
        wl(("local function %s(%s) %s[%s.a]=%s(%s,%s);end"):format(H[26],ha(),aREGS,aINS,vLT,nv("b"),nv("c")))
        emit_junk()
        wl(("local function %s(%s) %s[%s.a]=%s(%s,%s);end"):format(H[27],ha(),aREGS,aINS,vLE,nv("b"),nv("c")))
        wl(("local function %s(%s) %s[%s.a]=%s(%s,%s);end"):format(H[28],ha(),aREGS,aINS,vGT,nv("b"),nv("c")))
        emit_junk()
        wl(("local function %s(%s) %s[%s.a]=%s(%s,%s);end"):format(H[29],ha(),aREGS,aINS,vGE,nv("b"),nv("c")))
        wl(("local function %s(%s) %s[%s.a]=%s(%s);end"):format(H[30],ha(),aREGS,aINS,vNOT,nv("b")))
        emit_dead_end()
    end
    do
        local vT=R() local vI=R()
        wl(("local function %s(%s) local %s={};for %s=0x0,%s.b-0b1 do %s[#%s+0b1]=%s(%s[%s.a+%s])end;print(%s[%s](%s,%s))end"):format(H[31],ha(),vT,vI,aINS,vT,vT,_tostring,aREGS,aINS,vI,_table,encodeString("concat"),vT,encodeString("\t")))
    end
    wl(("local function %s(%s) %s.%s=%s.%s+%s.a;end"):format(H[32],ha(),aFRM,fPC,aFRM,fPC,aINS))
    wl(("local function %s(%s) if(not %s[%s.a])then %s.%s=%s.%s+%s.b;end;end"):format(H[33],ha(),aREGS,aINS,aFRM,fPC,aFRM,fPC,aINS))
    wl(("local function %s(%s) %s[%s.a]=%s(%s[%s.b],%s,%s,%s,%s.%s or {},%s)end"):format(H[34],ha(),aREGS,aINS,vNEWCLOS,aPROTOS,aINS,aENV,aCONSTS,aPROTOS,aFRM,fUPVALS,aREGS))
    do
        local vFN=R() local vARGS=R() local vRES=R() local vI=R() local vJ=R()
        wl(("local function %s(%s)"):format(H[35],ha()))
        wl(("  local %s=%s[%s.a]"):format(vFN,aREGS,aINS))
        wl(("  local %s={}"):format(vARGS))
        wl(("  for %s=0x1,%s.b-0b1 do %s[%s]=%s[%s.a+%s];end"):format(vI,aINS,vARGS,vI,aREGS,aINS,vI))
        wl(("  local %s={%s((%s[%s] or unpack)(%s))}"):format(vRES,vFN,_table,encodeString("unpack"),vARGS))
        wl(("  if(%s.c>0x01)then"):format(aINS))
        wl(("    for %s=0b1,%s.c-0x01 do %s[%s.a+%s-0x01]=%s[%s];end"):format(vJ,aINS,aREGS,aINS,vJ,vRES,vJ))
        wl("  end") wl("end")
    end
    do
        local vI=R() local vRES=R()
        wl(("local function %s(%s)"):format(H[36],ha()))
        wl(("  local %s={}"):format(vRES))
        wl(("  if(%s.b>0b1)then"):format(aINS))
        wl(("    for %s=0b0,%s.b-0x2 do %s[#%s+0B1]=%s[%s.a+%s];end"):format(vI,aINS,vRES,vRES,aREGS,aINS,vI))
        wl("  end")
        wl(("  %s.%s=true;%s.%s=%s"):format(aFRM,fSTOP,aFRM,fRETVALS,vRES))
        wl("end")
    end
    do
        local vFN=R() local vARGS=R() local vRES=R() local vI=R()
        wl(("local function %s(%s)"):format(H[37],ha()))
        wl(("  local %s=%s[%s.a]"):format(vFN,aREGS,aINS))
        wl(("  local %s={}"):format(vARGS))
        wl(("  for %s=0B1,%s.b-0x01 do %s[%s]=%s[%s.a+%s];end"):format(vI,aINS,vARGS,vI,aREGS,aINS,vI))
        wl(("  local %s={%s((%s[%s] or unpack)(%s))}"):format(vRES,vFN,_table,encodeString("unpack"),vARGS))
        wl(("  %s.%s=true;%s.%s=%s"):format(aFRM,fSTOP,aFRM,fRETVALS,vRES))
        wl("end")
    end
    do
        local vVA=R() local vI=R()
        wl(("local function %s(%s)"):format(H[38],ha()))
        wl(("  local %s=%s.%s or {}"):format(vVA,aFRM,fVARARGS))
        wl(("  if(%s.b==0b1)then"):format(aINS))
        wl(("    for %s=0x01,#%s do %s[%s.a+%s-0B1]=%s[%s];end"):format(vI,vVA,aREGS,aINS,vI,vVA,vI))
        wl("  else")
        wl(("    for %s=0b1,%s.b-0B1 do %s[%s.a+%s-0x01]=%s[%s];end"):format(vI,aINS,aREGS,aINS,vI,vVA,vI))
        wl("  end") wl("end")
    end
    do
        local vV=R() local vLM=R() local vST=R()
        wl(("local function %s(%s)"):format(H[39],ha()))
        wl(("  local %s,%s,%s=%s[%s.a],%s[%s.b],%s[%s.d]"):format(vV,vLM,vST,aREGS,aINS,aREGS,aINS,aREGS,aINS))
        wl(("  if(%s>0b0 and %s>%s)or(%s<0x0 and %s<%s)then %s.%s=%s.%s+%s.c;end"):format(vST,vV,vLM,vST,vV,vLM,aFRM,fPC,aFRM,fPC,aINS))
        wl("end")
    end
    do
        local vV=R() local vLM=R() local vST=R()
        wl(("local function %s(%s)"):format(H[40],ha()))
        wl(("  local %s=%s[%s.a]+%s[%s.d]"):format(vV,aREGS,aINS,aREGS,aINS))
        wl(("  %s[%s.a]=%s"):format(aREGS,aINS,vV))
        wl(("  local %s,%s=%s[%s.b],%s[%s.d]"):format(vLM,vST,aREGS,aINS,aREGS,aINS))
        wl(("  if(%s>0b0 and %s<=%s)or(%s<0x00 and %s>=%s)then %s.%s=%s.%s+%s.c;end"):format(vST,vV,vLM,vST,vV,vLM,aFRM,fPC,aFRM,fPC,aINS))
        wl("end")
    end
    wl(("local function %s(%s) %s.%s=true;end"):format(H[41],ha(),aFRM,fSTOP))
    do
        local vUV=R()
        wl(("local function %s(%s) local %s=%s.%s[%s.b];%s[%s.a]=(%s and %s[%s]);end"):format(H[42],ha(),vUV,aFRM,fUPVALS,aINS,aREGS,aINS,vUV,vUV,encodeString(fCELLV)))
    end
    do
        local vUV=R() local vVAL=R()
        wl(("local function %s(%s) local %s=%s.%s[%s.a];local %s=%s[%s.b];if(%s)then %s[%s]=%s;if(%s[%s] and %s[%s])then %s[%s][%s[%s]]=%s;end;end;end"):format(
            H[43],ha(), vUV,aFRM,fUPVALS,aINS, vVAL,aREGS,aINS, vUV, vUV,encodeString(fCELLV),vVAL, vUV,encodeString(fOPEN),vUV,encodeString(fREGS), vUV,encodeString(fREGS),vUV,encodeString(fREG),vVAL))
    end
    wl(("local function %s(%s) %s[%s.a]=%s(%s.b);if(%s.c~=%s.a)then %s[%s.c]=%s[%s.a]end;end"):format(
        H[44], ha(), aREGS, aINS, aCONSTS, aINS, aINS, aINS, aREGS, aINS, aREGS, aINS))
    wl(("local function %s(%s) %s[%s.a]=%s[%s.b];%s[%s.c]=%s[%s.b];end"):format(
        H[45], ha(), aREGS, aINS, aREGS, aINS, aREGS, aINS, aREGS, aINS))
    wl(("local function %s(%s) %s[%s.a]=nil;%s[%s.b]=nil;end"):format(
        H[46], ha(), aREGS, aINS, aREGS, aINS))
    wl(("local function %s(%s) %s[%s.a]=not not %s;end"):format(
        H[47], ha(), aREGS, aINS, nv("b")))
    do
        local vOP = R()
        wl(("local function %s(%s)"):format(H[48], ha()))
        wl(("  local %s=%s.d"):format(vOP, aINS))
        wl(("  if(%s==9)then %s[%s.a]=%s+%s(%s.c)"):format(vOP, aREGS, aINS, nv("b"), aCONSTS, aINS))
        wl(("  elseif(%s==10)then %s[%s.a]=%s-%s(%s.c)"):format(vOP, aREGS, aINS, nv("b"), aCONSTS, aINS))
        wl(("  elseif(%s==11)then %s[%s.a]=%s*%s(%s.c)"):format(vOP, aREGS, aINS, nv("b"), aCONSTS, aINS))
        wl(("  elseif(%s==12)then %s[%s.a]=%s/%s(%s.c) end"):format(vOP, aREGS, aINS, nv("b"), aCONSTS, aINS))
        wl("end")
    end
    wl(("local function %s(%s) %s[%s.a]=%s[%s.b][%s(%s.c)];end"):format(
        H[49], ha(), aREGS, aINS, aREGS, aINS, aCONSTS, aINS))
    wl(("local function %s(%s) %s[%s.a][%s(%s.b)]=%s[%s.c];end"):format(
        H[50], ha(), aREGS, aINS, aCONSTS, aINS, aREGS, aINS))
    wl(("local function %s(%s) %s[%s.a]=%s(%s.b);%s[%s.c]=%s(%s.d);end"):format(
        H[51], ha(), aREGS, aINS, aCONSTS, aINS, aREGS, aINS, aCONSTS, aINS))
    wl(("local function %s(%s) %s[%s.a]=%s[%s.b];%s[%s.c]=%s(%s.d);end"):format(
        H[52], ha(), aREGS, aINS, aREGS, aINS, aREGS, aINS, aCONSTS, aINS))

    do
        local NB = math.random(5, 8)
        local order = {}
        for i = 0, NUM_OPCODES - 1 do order[#order + 1] = i end
        for i = #order, 2, -1 do
            local j = math.random(1, i)
            order[i], order[j] = order[j], order[i]
        end
        local buckets = {}
        for b = 1, NB do buckets[b] = {} end
        for idx, real_op in ipairs(order) do
            local b = ((idx - 1) % NB) + 1
            buckets[b][#buckets[b] + 1] = real_op
        end
        for b = 1, NB do
            local t = buckets[b]
            for i = #t, 2, -1 do
                local j = math.random(1, i)
                t[i], t[j] = t[j], t[i]
            end
        end

        local hash = 0
        for _, real_op in ipairs(order) do
            local name = H[real_op]
            for k = 1, #name do
                hash = (hash * 131 + name:byte(k) + op_perm[real_op]) % 4294967296
            end
        end
        hash = (hash ~ integrity_key) % 4294967296
        local vDISPHASH = R()
        wl(("local %s=%d"):format(vDISPHASH, hash))

        local vORIGEXEC = R()
        local sample_handlers = {}
        for i = 1, math.min(8, #order) do
            sample_handlers[#sample_handlers + 1] = H[order[i]]
        end
        local vORIGHS = {}
        for i = 1, #sample_handlers do vORIGHS[i] = R() end
        wl(("local %s"):format(vORIGEXEC))
        for i = 1, #sample_handlers do
            wl(("local %s"):format(vORIGHS[i]))
        end

        local bucket_fns = {}
        for b = 1, NB do
            bucket_fns[b] = R()
        end
        for b = 1, NB do
            local pINS, pREGS, pCON, pENV, pFRM, pPROTOS = R(), R(), R(), R(), R(), R()
            local pOP = R()
            wl(("local function %s(%s,%s,%s,%s,%s,%s)"):format(
                bucket_fns[b], pINS, pREGS, pCON, pENV, pFRM, pPROTOS))
            wl(("  local %s=%s.%s"):format(pOP, pINS, fOP))
            if math.random() < 0.5 then
                local jv = R()
                wl(("  if%sthen local %s=%d;end"):format(opaque_false(), jv, math.random(1,40)))
            end
            local ops_in_bucket = buckets[b]
            for idx, real_op in ipairs(ops_in_bucket) do
                local perm_val = op_perm[real_op]
                local handler = H[real_op]
                if idx == 1 then
                    wl(("  if(%s==%d)then %s(%s,%s,%s,%s,%s,%s)")
                        :format(pOP, perm_val, handler, pINS, pREGS, pCON, pENV, pFRM, pPROTOS))
                else
                    wl(("  elseif(%s==%d)then %s(%s,%s,%s,%s,%s,%s)")
                        :format(pOP, perm_val, handler, pINS, pREGS, pCON, pENV, pFRM, pPROTOS))
                end
                if math.random() < 0.22 then
                    local jv = R()
                    wl(("  elseif(%s)then local %s=%d;%s=nil")
                        :format(opaque_false(), jv, math.random(1,99), jv))
                end
            end
            wl("  end")
            if math.random() < 0.4 and NB > 1 then
                local other = bucket_fns[math.random(1, NB)]
                if other ~= bucket_fns[b] then
                    wl(("  if%sthen %s(%s,%s,%s,%s,%s,%s)end")
                        :format(opaque_false(), other, pINS, pREGS, pCON, pENV, pFRM, pPROTOS))
                end
            end
            wl("end")
            emit_junk()
        end

        local vROUTE = R()
        local vRMAP = R()
        wl(("local %s={}"):format(vRMAP))
        local route_pairs = {}
        for b = 1, NB do
            for _, real_op in ipairs(buckets[b]) do
                route_pairs[#route_pairs + 1] = { op_perm[real_op], b }
            end
        end
        for i = #route_pairs, 2, -1 do
            local j = math.random(1, i)
            route_pairs[i], route_pairs[j] = route_pairs[j], route_pairs[i]
        end
        for _, pair in ipairs(route_pairs) do
            local pov, b = pair[1], pair[2]
            wl(("%s[%d]=%d"):format(vRMAP, pov, b))
            if math.random() < 0.25 then
                local jv = R()
                wl(("do local %s=%d;%s=%s*%d;end"):format(jv, math.random(2,30), jv, jv, math.random(2,5)))
            end
            if math.random() < 0.12 then
                wl(("%s[%d]=%d"):format(vRMAP, math.random(200, 400), math.random(1, NB)))
            end
        end
        emit_heavy_mt(vRMAP, "lock")
        wl(("local function %s(o)"):format(vROUTE))
        wl(("  local r=%s[o]"):format(vRMAP))
        wl(("  if%sthen r=r or 0B1;end"):format(opaque_true()))
        wl(("  if%sthen r=(r or 0x0)+0x00;end"):format(opaque_false()))
        wl(("  return r or 0x01"))
        wl("end")

        local vCODE2=R() local vCON2=R() local vENV2=R()
        local vREGS=R() local vFRM=R() local vINS2=R()
        local vPROTOS2=R() local vVARARGS=R() local vUPVALS=R() local vNPARAMS=R()
        local vPi=R() local vExtraVA=R()
        local vOPVAL=R() local vBID=R()
        wl(("%s=function(%s,%s,%s,%s,%s,%s,%s)"):format(vEXEC,vCODE2,vCON2,vENV2,vPROTOS2,vVARARGS,vUPVALS,vNPARAMS))
        wl(("  local %s={}"):format(vREGS))
        wl(("  local %s=%s or {}"):format(vVARARGS, vVARARGS))
        wl(("  local %s=%s or 0b0"):format(vNPARAMS, vNPARAMS))
        wl(("  for %s=0b1,%s do %s[%s-0b1]=%s[%s];end"):format(vPi,vNPARAMS,vREGS,vPi,vVARARGS,vPi))
        wl(("  local %s={}"):format(vExtraVA))
        wl(("  for %s=%s+0b1,#%s do %s[#%s+0b1]=%s[%s];end"):format(vPi,vNPARAMS,vVARARGS,vExtraVA,vExtraVA,vVARARGS,vPi))
        wl(("  local %s={%s=0b1,%s=false,%s=%s,%s=%s or {}}"):format(vFRM,fPC,fSTOP,fVARARGS,vExtraVA,fUPVALS,vUPVALS))
        local vDEAD=R() local vISTATE=R() local vTmpJ=R() local vTmpK=R() local vFake=R()
        local vCFCHECK=R() local vPOISON=R() local vEXP=R() local vLIVE=R() local vCI=R()
        local vSTATE = R()
        local S_FETCH     = math.random(10, 40)
        local S_INTEGRITY = S_FETCH + math.random(15, 40)
        local S_DISPATCH  = S_INTEGRITY + math.random(15, 40)
        local S_ADVANCE   = S_DISPATCH + math.random(15, 40)
        local S_EXIT      = S_ADVANCE + math.random(15, 40)
        local S_DEAD1     = S_EXIT + math.random(20, 50)
        local S_DEAD2     = S_DEAD1 + math.random(15, 35)
        local S_DEAD3     = S_DEAD2 + math.random(15, 35)
        local S_DEAD4     = S_DEAD3 + math.random(15, 35)

        wl(("  local %s=0x0"):format(vEXP))
        wl(("  for %s=0b1,#%s do"):format(vCI,vCODE2))
        wl(("    local %s=%s[%s]"):format(vINS2,vCODE2,vCI))
        wl(("    if(%s)then %s=%s[%s](%s*0x1000193+%s.%s+%s.a+%s.b+%s.c+%s,4294967296)end"):format(
            vINS2,vEXP,_math,encodeString("fmod"),vEXP,vINS2,fOP,vINS2,vINS2,vINS2,vCI))
        wl("  end")
        wl(("  %s=%s[%s](%s,%s)"):format(vEXP,_bit,encodeString("bxor"),vEXP,vINTKEY))
        wl(("  local %s=%s"):format(vISTATE,vINTKEY))
        local cf_ops = {32,33,36,37,39,40,41}
        local cf_perm = {}
        for _,o in ipairs(cf_ops) do cf_perm[#cf_perm+1] = op_perm[o] end
        wl(("  local %s={[%s]=true,[%s]=true,[%s]=true,[%s]=true,[%s]=true,[%s]=true,[%s]=true}"):format(
            vCFCHECK, cf_perm[1], cf_perm[2], cf_perm[3], cf_perm[4], cf_perm[5], cf_perm[6], cf_perm[7]))

        wl(("  local %s"):format(vINS2))
        wl(("  local %s,%s"):format(vOPVAL, vBID))
        wl(("  local %s=%d"):format(vSTATE, S_FETCH))
        local vCFCNT = R()
        local INTEGRITY_EVERY = math.random(35, 70)
        wl(("  local %s=0"):format(vCFCNT))

        wl("  while true do")

        wl(("    if(%s==%d)then"):format(vSTATE, S_FETCH))
        wl(("      %s=%s[%s.%s]"):format(vINS2,vCODE2,vFRM,fPC))
        wl(("      if(%s==nil)or(%s.%s)then"):format(vINS2,vFRM,fSTOP))
        wl(("        %s=%d"):format(vSTATE, S_EXIT))
        wl("      else")
        wl(("        %s=%s[%s](%s*0x1000193+%s.%s+%s.a*0x1F+%s.b*0x11+%s.c*0x0D+%s.%s,4294967296)"):format(
            vISTATE,_math,encodeString("fmod"),vISTATE,vINS2,fOP,vINS2,vINS2,vINS2,vFRM,fPC))
        wl(("        %s=%s[%s](%s,%s)"):format(vISTATE,_bit,encodeString("bxor"),vISTATE,vINTKEY))
        wl(("        if%sthen local %s=%s*%d;%s=%s+%d;end"):format(
            opaque_true(vISTATE), vTmpJ, vISTATE, math.random(2,6), vTmpJ, vTmpJ, math.random(1,4)))
        wl(("        if%sthen local %s=%s;end"):format(opaque_false(vISTATE), vTmpK, vISTATE))
        wl(("        if%sthen %s=%d else %s=%d end"):format(
            opaque_true(vISTATE), vSTATE, S_INTEGRITY, vSTATE, S_DEAD1))
        wl("      end")

        wl(("    elseif(%s==%d)then"):format(vSTATE, S_INTEGRITY))
        wl(("      %s=%s+1"):format(vCFCNT, vCFCNT))
        wl(("      if(%s[%s.%s])and(%s%%%d==0)then"):format(vCFCHECK,vINS2,fOP, vCFCNT, INTEGRITY_EVERY))
        wl(("        local %s=0x0"):format(vLIVE))
        wl(("        for %s=0b1,#%s do"):format(vCI,vCODE2))
        wl(("          local %s=%s[%s]"):format(vPOISON,vCODE2,vCI))
        wl(("          if(%s)then %s=%s[%s](%s*0x1000193+%s.%s+%s.a+%s.b+%s.c+%s,4294967296)end"):format(
            vPOISON,vLIVE,_math,encodeString("fmod"),vLIVE,vPOISON,fOP,vPOISON,vPOISON,vPOISON,vCI))
        wl("        end")
        wl(("        %s=%s[%s](%s,%s)"):format(vLIVE,_bit,encodeString("bxor"),vLIVE,vINTKEY))
        wl(("        if(%s~=%s)or(%s~=%s)"):format(vLIVE,vEXP,vEXEC,vORIGEXEC))
        for i = 1, #sample_handlers do
            wl(("        or(%s~=%s)"):format(sample_handlers[i], vORIGHS[i]))
        end
        wl("        then")
        wl(("          %s.%s=true;%s=nil;%s=function()end;%s=%d"):format(vFRM,fSTOP,vREGS,vEXEC,vSTATE,S_EXIT))
        wl("        else")
        wl(("          %s=%d"):format(vSTATE, S_DISPATCH))
        wl("        end")
        wl("      else")
        wl(("        %s=%d"):format(vSTATE, S_DISPATCH))
        wl("      end")
        wl(("      if%sthen %s=%d end"):format(opaque_false(vISTATE), vSTATE, S_DEAD2))

        wl(("    elseif(%s==%d)then"):format(vSTATE, S_DISPATCH))
        wl(("      local %s=(%s and %d or %d);if(%s>%d)then %s=%s;end"):format(
            vFake, opaque_true(vISTATE), math.random(1,5), math.random(6,12), vFake, 20, vFake, vISTATE))
        wl(("      %s=%s.%s"):format(vOPVAL, vINS2, fOP))
        wl(("      %s=%s(%s)"):format(vBID, vROUTE, vOPVAL))
        wl(("      if%sthen %s=%s+0b0;end"):format(opaque_false(vISTATE), vBID, vBID))
        local bucket_order = {}
        for b = 1, NB do bucket_order[b] = b end
        for i = NB, 2, -1 do
            local j = math.random(1, i)
            bucket_order[i], bucket_order[j] = bucket_order[j], bucket_order[i]
        end
        for idx, b in ipairs(bucket_order) do
            local fn = bucket_fns[b]
            if idx == 1 then
                wl(("      if(%s==%d)then %s(%s,%s,%s,%s,%s,%s)")
                    :format(vBID, b, fn, vINS2, vREGS, vCON2, vENV2, vFRM, vPROTOS2))
            else
                wl(("      elseif(%s==%d)then %s(%s,%s,%s,%s,%s,%s)")
                    :format(vBID, b, fn, vINS2, vREGS, vCON2, vENV2, vFRM, vPROTOS2))
            end
            if math.random() < 0.3 then
                local jv = R()
                wl(("      elseif(%s)then local %s=%d;%s=%d")
                    :format(opaque_false(vISTATE), jv, math.random(1,50), vSTATE, S_DEAD3))
            end
        end
        wl("      end")
        wl(("      if%sthen %s=%d else %s=%d end"):format(
            opaque_true(vISTATE), vSTATE, S_ADVANCE, vSTATE, S_DEAD4))

        wl(("    elseif(%s==%d)then"):format(vSTATE, S_ADVANCE))
        wl(("      if(not %s.%s)then %s.%s=%s.%s+0B1;end"):format(vFRM,fSTOP,vFRM,fPC,vFRM,fPC))
        wl(("      if%sthen %s=%s+0x0;end"):format(opaque_false(vISTATE), vISTATE, vISTATE))
        wl(("      %s=%d"):format(vSTATE, S_FETCH))

        wl(("    elseif(%s==%d)then"):format(vSTATE, S_EXIT))
        wl("      break")

        wl(("    elseif(%s==%d)then"):format(vSTATE, S_DEAD1))
        wl(("      local %s=%d;%s=%s*%d"):format(vTmpJ, math.random(3,40), vTmpJ, vTmpJ, math.random(2,7)))
        wl(("      if%sthen %s=%d else %s=%d end"):format(
            opaque_false(vISTATE), vSTATE, S_DEAD2, vSTATE, S_FETCH))

        wl(("    elseif(%s==%d)then"):format(vSTATE, S_DEAD2))
        wl(("      local %s=%s"):format(vTmpK, vISTATE))
        wl(("      if%sthen %s=%d else %s=%d end"):format(
            opaque_true(vISTATE), vSTATE, S_INTEGRITY, vSTATE, S_DEAD3))

        wl(("    elseif(%s==%d)then"):format(vSTATE, S_DEAD3))
        wl(("      if%sthen %s=%d end"):format(opaque_false(vISTATE), vSTATE, S_DISPATCH))
        wl(("      %s=%d"):format(vSTATE, S_ADVANCE))

        wl(("    elseif(%s==%d)then"):format(vSTATE, S_DEAD4))
        wl(("      local %s=(%s and %d or %d)"):format(vFake, opaque_false(vISTATE), math.random(1,9), math.random(10,20)))
        wl(("      %s=%d"):format(vSTATE, S_FETCH))

        wl("    else")
        wl(("      %s=%d"):format(vSTATE, S_EXIT))
        wl("    end")

        wl("  end")
        wl(("  return %s.%s or {}"):format(vFRM,fRETVALS))
        wl("end")
        wl(("%s=%s"):format(vORIGEXEC, vEXEC))
        for i = 1, #sample_handlers do
            wl(("%s=%s"):format(vORIGHS[i], sample_handlers[i]))
        end
        wl(("if%sthen local _=%s;end"):format(opaque_false(), vDISPHASH))
    end
    dynamic()
    emit_junk()
    emit_dead_end()
    wl(("local %s=%s(%s(%s(%s(%s)),%s))"):format(vTMP,vLZDEC,vXORDEC,vUNPACK,vDEA85,vPAYLOAD,vKEY))
    emit_junk()
    emit_dead_end()
    local vTYPES=R()
    wl(("local %s,%s,%s=%s(%s,0x01)"):format(vCONSTS,vP,vTYPES,vRCONSTS,vTMP))
    do
        local vENC   = R()
        local vCACHE = R()
        local vGET   = R()
        local vIDX   = R()
        local vC     = R()
        local vENC2  = R()
        local vPLAIN = R()
        local vTYP   = R()

        wl(("local %s=%s"):format(vENC, vCONSTS))
        wl(("local %s={}"):format(vCACHE))
        wl(("local function %s(%s)"):format(vGET, vIDX))
        wl(("  local %s=%s[%s]"):format(vC, vCACHE, vIDX))
        wl(("  if(%s~=nil)then return %s end"):format(vC, vC))
        wl(("  local %s=%s[%s]"):format(vENC2, vENC, vIDX))
        wl(("  if(%s==nil)then return nil end"):format(vENC2))
        wl(("  local %s=%s(%s,%s)"):format(vPLAIN, vCDECRYPT, vENC2, vIDX))
        wl(("  local %s=%s[%s]"):format(vTYP, vTYPES, vIDX))
        wl(("  if(%s==0x0)then %s=%s(%s) end"):format(vTYP, vPLAIN, _tonumber, vPLAIN))
        wl(("  %s[%s]=%s"):format(vCACHE, vIDX, vPLAIN))
        wl(("  return %s"):format(vPLAIN))
        wl("end")
        wl(("%s=%s"):format(vCONSTS, vGET))
        emit_heavy_mt(vENC, "lock")
        wl(("if%sthen local _=%s(%d) end"):format(opaque_false(), vGET, math.random(0, 3)))
        wl(("if%sthen local _=%s(%d) end"):format(opaque_false(), vGET, math.random(0, 3)))
    end
    emit_junk()
    emit_dead_end()
    wl(("local %s;%s,%s=%s(%s,%s)"):format(vPROTOS,vPROTOS,vP,vRPROTOS,vTMP,vP))
    wl(("local %s,%s=%s(%s,%s)"):format(vCODE,vP,vRCODE,vTMP,vP))
    emit_heavy_mt(vCODE, "lock")
    emit_junk()
    emit_heavy_mt(vPROTOS, "lock")
    emit_junk()
    emit_dead_end()
    do
        local vG1 = R()
        wl(("local %s=%d;%s=%s*%d"):format(vG1, math.random(3,20), vG1, vG1, math.random(2,7)))
        wl(("if%sthen %s=%s+0x1;%s=nil;end"):format(opaque_false(), vG1, vG1, vG1))
    end
    do
        local vENV = R()
        wl(("local %s=%s()"):format(vENV, _fenv))
        do
            local vMT3 = R() local vTOSTR3 = R() local vLOCK3 = R() local vPOISON3 = R()
            wl(("local %s=function()return %s end"):format(vTOSTR3, encodeString("")))
            wl(("local %s={}"):format(vLOCK3))
            wl(("local %s=function()return %d end"):format(vPOISON3, math.random(0,3)))
            wl(("pcall(function()setmetatable(%s,{[%s]=%s,[%s]=%s,[%s]=function()return function()end end,[%s]=function()return function()end end,[%s]=function()return 0 end,[%s]=function()return %s()end,[%s]=function()return false end})end)"):format(
                vLOCK3,
                encodeString("__metatable"), vLOCK3,
                encodeString("__tostring"), vTOSTR3,
                encodeString("__pairs"),
                encodeString("__ipairs"),
                encodeString("__len"),
                encodeString("__call"), vPOISON3,
                encodeString("__eq")))
            wl(("local %s={[%s]=%s,[%s]=%s,[%s]=function()return function()end;end,[%s]=function()return function()end;end,[%s]=function()return 0x0;end,[%s]=function()return %s()end,[%s]=function()return false;end,[%s]=function()return false;end,[%s]=function()return true;end}"):format(
                vMT3,
                encodeString("__tostring"), vTOSTR3,
                encodeString("__metatable"), vLOCK3,
                encodeString("__pairs"),
                encodeString("__ipairs"),
                encodeString("__len"),
                encodeString("__call"), vPOISON3,
                encodeString("__eq"),
                encodeString("__lt"),
                encodeString("__le")))
            wl(("pcall(function()setmetatable(%s,%s)end)"):format(vENV, vMT3))
        end
        dynamic()
        emit_junk()
        emit_dead_end()

        local needs_b64 = false
        for _, nv_entry in ipairs(NOVIRTUALIZEs) do
            if nv_entry.needs_b64 then needs_b64 = true; break end
        end
        if needs_b64 then
            wl(("local function %s(s)"):format(vB64D))
            wl(("  local b=%s"):format(encodeString("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/")))
            wl(("  local t={} for i=0b1,#b do t[b[%s](b,i,i)]=i-0x01;end"):format(encodeString("sub")))
            wl("  local o={};local n=0b0 local r=0B0")
            wl("  for i=0b1,#s do")
            wl(("    local c=s[%s](s,i,i)"):format(encodeString("sub")))
            wl(("    if(c~=%s)then"):format(encodeString("=")))
            wl("      local v=t[c]")
            wl("      if(v)then")
            wl("        n=n*6_4+v;r=r+0x06")
            wl("        if(r>=0x08)then")
            wl("          r=r-0x08")
            wl(("          o[#o+0b1]=%s[%s](%s[%s](n/0x02^r)%%2_56)"):format(_string,encodeString("char"),_math,encodeString("floor")))
            wl("          n=n%(0b10^r)")
            wl("        end")
            wl("      end")
            wl("    end")
            wl("  end")
            wl(("  return %s(o)"):format(man_c))
            wl("end")
            emit_junk()
        end

        for _, nv_entry in ipairs(NOVIRTUALIZEs) do
            local fname = R()
            wl(("local %s"):format(fname))
            wl(("if%sthen"):format(opaque_true()))
            wl(("%s=%s"):format(fname, nv_entry.body))
            wl(("else %s=function()end;end"):format(fname))
            wl(("if%sthen local %s=%d;%s=%s*%d;end"):format(
                opaque_false(), R(), math.random(3,40), R(), R(), math.random(2,7)))
            wl(("%s[%s]=%s"):format(vENV, encodeString(nv_entry.name), fname))
            emit_junk()
        end
        dynamic()
        wl(("%s(%s,%s,%s,%s,{},{},0b0)"):format(vEXEC, vCODE, vCONSTS, vENV, vPROTOS))
    end
    return table.concat(out,"\n")
end


local src
do
    local path = arg and arg[1]
    if not path then
        error("No input path. Usage: lua bakeowieh.lua <input.lua> [seed]")
    end
    local f = io.open(path, "r")
    if not f then
        error("Could not open input file: " .. tostring(path))
    end
    src = f:read("*a")
    f:close()
end

if not src or src == "" then
    error("Input file was empty")
end

local smth=[==[
loadstring([=[--[[
        .@%(/*,.......      ...,,*/(#%&@@.\
        (*   ,/(#%%&&@@@@&%((////(((##%###((/**,,.     ,//(&.\
        /* .%@@@@@@@@%,  .(&@@@&&&&&&@@@@@@&#(*,........*%@@@(.  ,#.\
        */ .&@@@@@@@*  (%,   *(&&@@@@@&%(*,.             .,*(#%(*@@&*  *,\
        #, /@@@@@@* *&( ,&&/.,/#%&&@@@&(&@@@@@@@@@@@@#*,.....,/&@@@@@@@@( .%\
        #  #@@@@@*/@% .#%./(,.,/*,//*,.,/(*@@@@@@@@@@@@%@@@@@@@@@#.#@@@@@@&. %\
        /  &@@@@@@@@(%@# *&&*&@@@@#/&@@@@/%%.,%@@@@@@@%/@@&(,  ,,,...  *%@@@# *\
        #  .&@@@@@@@@@@@,((%@@@@@#.    ,&@@#@@&* .&@@@@@&,.#@@@@/&@@%(@@@&(/,(&, /,\
        (/   (@&&&%&@@@&/, ,@#(@@@@,        #@@/,&@& /@@@@@,%#%@@@@@(     *@@@@@&,%%. .\
        /  #/,#@@@&#(//#@@@/ %@@@&@@@(.    ,&@@(.*/*  %@@*   %@@@@@@%       (@@&(*...%&.\
        ///@@&,  (&@@#,   /@/ ,*&@@@@#&@@%#%((%@&* /@@@@@@&. #@@@#&@@@&%%@@@@@@&,/(*@/#\
        %%.&@# .&@@@# /@@@@%&@@@&/.   ,/((/*,  ./&@@@@@@@@@@,*&(./%@@#*&@@@(#(....,&#*@/\
        @%.&& .&@@@&*    /&@@@@@@@@@@@@@@@@&@@#/(%@@@@@@@@@@&,  (@@@@@@@@@@@@/,@@@@@#.&*\
        &&,%% .&*    /@@@(.  ,(@@@@@&/(////#( /&@@@@@@@@@@@@@@@(  ,&@@@@@@@@&, (@@&*/@(/\
        .%*#@( /@@@@( *@@@@@@/     *%@@@@@@@&.,@& ,#, .&@@@@@@# .#*%&/,#@@@@*   *@@&/*&*\
        .&/.#@@@@@@@,   *&@@%.,&@@&(,    ,(%@%&@@@@@@@@@(.*,  /@@@@@@@@@&,      %@@@@..\
        @* .%@@@@@@@@(       .   (@@@@@@@@(       .*(%&@@@@@@@@@@@@&(,  ./.*@%   /@@% ./\
        @* .&@@@@@@&.             ./&@@@*.&@@@@@@@&, ,**,.    .,*(&(.%@@# %@*  ,@@% ,#\
        &, /@@@@@@*                    .#@@@@@@@@*.%@@@@@(,@@@@@@& ,%(.      .&@% ,#\
        / *@@@@@#                                                           %@&.,#\
        (( .&@@@@*                                                          #@&.,#\
        .&. ,&@@@,                                                         (@&.,#\
        #. .%@@* /@@/                                                   /@&.,(\
        ./  #@%. %@&,,#,                                              /@@,./\
        *(  #@%. . (@@@@@%/,                                        /@@,.*\
        //  %@&, *@@@@@@@@( (@%/.                                 #@@, (\
        #* .&@@#. (@@@@&.*@@@@@@@@%. */.                  *..%*.&@@, /\
        @* .%@@@%, ,/ .@@@@@@@@@@,.%@@@@@% .&@@@* #@&..&@*,* %@@&. *\
        /  *&@@@@%,   *(&@@@@&. #@@@@@* #@@@% (@@* ,.   /@@@@* (\
        @#. .#@@@@@@&(,.                      .,*(%&@@@@@&..(\
        &(.   ./%@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@(. ((\
        ,#/*.       ..,,,,,,,,....          ,/#\
        ]]--]=])()
do
local x=1
local d=2
local ok,err=pcall(function()
local cr=Instance['new']('Frame')
cr.Size=UDim2['new'](0,0,0,0)
cr.Parent=workspace['This script is protected with Kryos v16.0, by feariosz0']
end)
if(ok)then
x=nil
end
local ch = game:GetService("Players").LocalPlayer.Character
if ch then
    local to = ch:FindFirstChild("Torso") or ch:FindFirstChild("UpperTorso")
    if to and to:FindFirstChild("Neck") then
        local randomAngle = math.random(10000, 99999)
        to.Neck.CurrentAngle = randomAngle
        task.wait(0.1)
        if to.Neck.CurrentAngle == randomAngle then
            x = nil
        end
    end
end
local nj=Path2DControlPoint['new']
local string='jdjwjwjsnsndjsqoozyxvwna'
local EncodingService=game['GetService'](game,'EncodingService')
local inputBuf=buffer['create'](#string)
buffer['writestring'](inputBuf,0,string)
local compressed=EncodingService['CompressBuffer'](EncodingService,inputBuf,Enum['CompressionAlgorithm']['Zstd'],3)
local encoded=buffer['tostring'](EncodingService['Base64Encode'](EncodingService,compressed))
if(encoded~='KLUv/SAYwQAAamRqd2p3anNuc25kanNxb296eXh2d25h')then
x=nil
end
x=x+d
end
]==]

src=smth.."\n"..src

local cgd = gen_id()

local seed = os.time()
if arg and arg[2] then
    local n = tonumber(arg[2])
    if n then seed = n end
end

local seed = os.time()
local result = obf.obfuscate(src, seed)
result = result:gsub("\n", " ")
result = result:gsub("%s+", " ")
result = result:gsub("%) end", ")end")
result = result:gsub(" end", ";end")
result = result:gsub('{ ', '{')
result = result:gsub(' }', '}')
result = result:gsub(' = ', '=')
local dx = ("-- This script is protected with Kryos v16.0, by feariosz0\nreturn setmetatable({%s=function(%s,%s,%s,%s,%s,%s,%s,%s,%s)%send},{}):%s()"):format(cgd,_tostring,_tonumber,_fenv,_bit,_math,_string,_table,_type,_of,result,cgd)
result = dx
print(result)
