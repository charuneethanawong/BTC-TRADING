//+------------------------------------------------------------------+
//|                                                        JAson.mqh |
//|                     Copyright 2015-2017, Keisuke (T3_E) Kinukawa |
//|                                           https://www.mql5.com/  |
//+------------------------------------------------------------------+
#property copyright "Copyright 2015-2017, Keisuke (T3_E) Kinukawa"
#property link      "https://www.mql5.com/en/code/13594"
#property strict

enum ENUM_JTYPE {JNOTYPE, JOBJECT, JARRAY, JSTRING, JNUMBER, JBOOL, JNULL};

class CJAVal
{
public:
   CJAVal* m_parent;
   CJAVal* m_list[];
   string m_key;
   string m_s;
   double m_n;
   bool m_b;
   ENUM_JTYPE m_type;

   CJAVal() { m_parent = NULL; m_type = JNOTYPE; Clear(); }
   CJAVal(CJAVal* parent, ENUM_JTYPE type) { m_parent = parent; m_type = type; Clear(); }
   ~CJAVal() { Clear(); }

   void Clear()
   {
      m_key = ""; m_s = ""; m_n = 0; m_b = false;
      for(int i = 0; i < ArraySize(m_list); i++) { if(CheckPointer(m_list[i]) == POINTER_DYNAMIC) delete m_list[i]; }
      ArrayResize(m_list, 0);
   }

   CJAVal* operator[](string key)
   {
      if(m_type != JOBJECT && m_type != JNOTYPE) return NULL;
      m_type = JOBJECT;
      for(int i = 0; i < ArraySize(m_list); i++) { if(m_list[i].m_key == key) return m_list[i]; }
      int sz = ArraySize(m_list);
      ArrayResize(m_list, sz + 1);
      m_list[sz] = new CJAVal(GetPointer(this), JNOTYPE);
      m_list[sz].m_key = key;
      return m_list[sz];
   }

   CJAVal* operator[](int i)
   {
      if(m_type != JARRAY && m_type != JNOTYPE) return NULL;
      m_type = JARRAY;
      int sz = ArraySize(m_list);
      if(i >= sz)
      {
         ArrayResize(m_list, i + 1);
         for(int j = sz; j <= i; j++) m_list[j] = new CJAVal(GetPointer(this), JNOTYPE);
      }
      return m_list[i];
   }

   void operator=(string s) { m_s = s; m_type = JSTRING; }
   void operator=(double n) { m_n = n; m_type = JNUMBER; }
   void operator=(int n) { m_n = n; m_type = JNUMBER; }
   void operator=(bool b) { m_b = b; m_type = JBOOL; }

   string ToStr() { return m_s; }
   double ToDbl() { return m_n; }
   int ToInt() { return (int)m_n; }
   bool ToBool() { return m_b; }

   bool Deserialize(string s) { int pos = 0; return Deserialize(s, pos); }
   bool Deserialize(string s, int& pos)
   {
      string key = m_key; // Preserve key if this is a nested object
      Clear();
      m_key = key;
      
      Skip(s, pos);
      if(pos >= StringLen(s)) return false;
      ushort c = StringGetCharacter(s, pos);
      if(c == '{') return ParseObject(s, pos);
      if(c == '[') return ParseArray(s, pos);
      if(c == '\"') return ParseString(s, pos);
      if(c == 't' || c == 'f') return ParseBool(s, pos);
      if(c == 'n') return ParseNull(s, pos);
      if((c >= '0' && c <= '9') || c == '-') return ParseNumber(s, pos);
      return false;
   }

private:
   void Skip(string s, int& pos)
   {
      while(pos < StringLen(s))
      {
         ushort c = StringGetCharacter(s, pos);
         if(c > ' ') break;
         pos++;
      }
   }

   bool ParseObject(string s, int& pos)
   {
      m_type = JOBJECT;
      pos++; // skip '{'
      while(pos < StringLen(s))
      {
         Skip(s, pos);
         if(StringGetCharacter(s, pos) == '}') { pos++; return true; }
         CJAVal* item = new CJAVal(GetPointer(this), JNOTYPE);
         if(!item.ParseKey(s, pos)) { delete item; return false; }
         Skip(s, pos);
         if(StringGetCharacter(s, pos) != ':') { delete item; return false; }
         pos++;
         if(!item.Deserialize(s, pos)) { delete item; return false; }
         int sz = ArraySize(m_list);
         ArrayResize(m_list, sz + 1);
         m_list[sz] = item;
         Skip(s, pos);
         ushort c = StringGetCharacter(s, pos);
         if(c == ',') pos++;
         else if(c == '}') { pos++; return true; }
         else return false;
      }
      return false;
   }

   bool ParseArray(string s, int& pos)
   {
      m_type = JARRAY;
      pos++; // skip '['
      while(pos < StringLen(s))
      {
         Skip(s, pos);
         if(StringGetCharacter(s, pos) == ']') { pos++; return true; }
         CJAVal* item = new CJAVal(GetPointer(this), JNOTYPE);
         if(!item.Deserialize(s, pos)) { delete item; return false; }
         int sz = ArraySize(m_list);
         ArrayResize(m_list, sz + 1);
         m_list[sz] = item;
         Skip(s, pos);
         ushort c = StringGetCharacter(s, pos);
         if(c == ',') pos++;
         else if(c == ']') { pos++; return true; }
         else return false;
      }
      return false;
   }

   bool ParseKey(string s, int& pos)
   {
      Skip(s, pos);
      if(StringGetCharacter(s, pos) != '\"') return false;
      return ParseString(s, pos, true);
   }

   bool ParseString(string s, int& pos, bool is_key = false)
   {
      if(!is_key) m_type = JSTRING;
      pos++; // skip '\"'
      int start = pos;
      while(pos < StringLen(s))
      {
         ushort c = StringGetCharacter(s, pos);
         if(c == '\"')
         {
            if(is_key) m_key = StringSubstr(s, start, pos - start);
            else m_s = StringSubstr(s, start, pos - start);
            pos++;
            return true;
         }
         if(c == '\\') pos++;
         pos++;
      }
      return false;
   }

   bool ParseBool(string s, int& pos)
   {
      m_type = JBOOL;
      if(StringSubstr(s, pos, 4) == "true") { m_b = true; pos += 4; return true; }
      if(StringSubstr(s, pos, 5) == "false") { m_b = false; pos += 5; return true; }
      return false;
   }

   bool ParseNull(string s, int& pos)
   {
      m_type = JNULL;
      if(StringSubstr(s, pos, 4) == "null") { pos += 4; return true; }
      return false;
   }

   bool ParseNumber(string s, int& pos)
   {
      m_type = JNUMBER;
      int start = pos;
      while(pos < StringLen(s))
      {
         ushort c = StringGetCharacter(s, pos);
         if((c < '0' || c > '9') && c != '.' && c != '-' && c != '+' && c != 'e' && c != 'E') break;
         pos++;
      }
      m_n = StringToDouble(StringSubstr(s, start, pos - start));
      return true;
   }
};

