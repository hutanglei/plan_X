# b1 收尾：修复未命中金句（替换为语料逐字片段）+ 处理换行金句
require "yaml"

CORPUS = "/Users/lnz/Documents/code-h/plan_X/majinghao_xueqiu/md"
IND    = "/Users/lnz/Documents/code-h/plan_X/.trae/skills/mjh-financial-analysis/knowledge/indicators"
PUNCT  = /[[:punct:][:space:]\u3000、。，；：？！“”‘’…·—–《》【】（）↑↓]/
NORM   = ->(s) { s.to_s.gsub(PUNCT, "") }

CORP = Dir.glob(File.join(CORPUS, "*.md")).map { |p| [p, File.read(p).gsub(PUNCT, "")] }
IN_CORPUS = ->(q) { q && !NORM.(q).empty? && CORP.any? { |_, n| n.include?(NORM.(q)) } }

# 每条修复: file => [ [old_key(归一子串定位), [候选金句数组...]], ... ]
# 候选 = 金句数组；取第一个"所有金句均逐字命中语料"的候选替换原行
FIX = {
  "big_bath" => [
    ["某些上市公司平时将亏损藏得严严实实", [
      ["某些上市公司平时将亏损藏得严严实实，虚增利润，并装模装样地发放股利，目的就是要符合增发条件，再融资圈钱。但是亏损终究藏不住，于是就集中在一个会计年度释放"]
    ]]
  ],
  "cash_to_profit" => [
    ["根据行业规律，净现比大于1.33时属于表现优秀", [
      ["根据行业规律，净现比大于1.33时属于表现优秀，最低不能小于0.5"],
      ["根据行业规律，净现比大于1.33时属于表现优秀"]
    ]]
  ],
  "deferred_tax" => [
    ["企业递延所得税负债项目如有余额，相当于占用税务局的钱", [
      ["企业递延所得税负债项目如有余额，相当于占用税务局的钱，这会在短期内减少公司现金流的流出，相当于一笔融资，对企业有利；企业递延所得税资产项目如有余额，相当于当期向税局多交了税"]
    ]]
  ],
  "eps" => [
    ["EPS即为每股盈余的意思", [
      ["EPS即为每股盈余的意思，和很多指标一样，它也有固定的计算公式，EPS=盈余/流通在外股数。"]
    ]]
  ],
  "fcf" => [
    ["咱们的现金流量表把利息支出放在筹资活动里", [
      ["咱们的现金流量表把利息支出放在筹资活动里（“分配股利、利润或偿付利息支付的现金”），经营现金流就没被利息污染过，所以“经营现金流−购建长期资产支出”这个简易口径，天然接近教科书上的FCFF，不用再费劲加回税后利息。"],
      ["咱们的现金流量表把利息支出放在筹资活动里（“分配股利、利润或偿付利息支付的现金”），经营现金流就没被利息污染过"]
    ]]
  ],
  "other_receivable" => [
    ["有一类会计科目，用途很广，会计看了偷笑，审计见了要哭", [
      ["有一类会计科目，用途很广，会计看了偷笑，审计见了要哭，它们有一个共同的名字叫“其他”，算算有“其他货币资金”、“其他应收款”、“其他应付款”、“其他业务收入”、“其他业务支出”等。"]
    ]]
  ],
  "roa" => [
    ["房地产、银行适合用ROA来评价", [
      ["房地产、银行适合用ROA来评价。这些企业负债都是非常高的。用ROE相对来讲，扭曲的成分较大。"],
      ["房地产、银行适合用ROA来评价", "这些企业负债都是非常高的。用ROE相对来讲，扭曲的成分较大。"],
      ["房地产、银行适合用ROA来评价", "用ROE相对来讲，扭曲的成分较大"]
    ]]
  ],
  "roe" => [
    ["ROE这个指标应该连续看5至10年以上", [
      ["平时我也做点股票，我会把过去连续 5~10 年平均 ROE 低于 15% 的企业全部排除掉，只关注那些长期平均 ROE＞15% 的企业。",
       "长期 ROE 高的公司不一定是优秀的公司，但是长期 ROE低 的公司一定不是优秀的公司！"]
    ]]
  ],
  "gross_vs_net_method" => [
    ["货是你自己的，你说了算", [
      ["货是你自己的，你说了算、砸手里算你的——你是老板，按总额法，收多少记多少。",
       "货是别人的，你就牵个线、赚个佣金——你是中间商，按净额法，只能记那点差价"],
      ["货是别人的，你就牵个线、赚个佣金——你是中间商，按净额法，只能记那点差价"]
    ]]
  ],
  "income_quality" => [
    ["一个真正优秀的企业，其未呈现为现金的利润是可以大量变现的", [
      ["一个真正优秀的企业，其未呈现为现金的利润是可以大量变现的。",
       "而一个财务造假的企业，其未呈现为现金的“利润”基本上是不可能变现的"]
    ]]
  ],
  "ponzi" => [
    ["把握庞氏工资的要点", [
      ["把握庞氏工资的要点：1.过高的工资；2.来源于筹资的部分"],
      ["1.过高的工资；2.来源于筹资的部分"]
    ]]
  ]
}

unwrap = ->(ln) {
  s = ln.strip.sub(/\A-\s*/, "")
  s = s[1..-2] if s.length >= 2 && ((s.start_with?("'") && s.end_with?("'")) || (s.start_with?('"') && s.end_with?('"')))
  s = s.gsub("\\n", "") # YAML 源文件中的字面 \n 转义（反斜杠+n），norm 前去除
  s
}

ok = 0
FIX.each do |f, fixes|
  path = File.join(IND, "#{f}.yaml")
  data = YAML.safe_load(File.read(path))
  lines = File.readlines(path)
  fixes.each do |key, candidates|
    qi = data["quotes"].index { |q| NORM.(q).include?(NORM.(key)) }
    unless qi
      puts "SKIP(quote未找到) #{f} :: #{key[0, 20]}"
      next
    end
    chosen = candidates.find { |cand| cand.all? { |c| IN_CORPUS.(c) } }
    unless chosen
      puts "FAIL(无连续原文候选) #{f} :: #{key[0, 20]}"
      next
    end
    old_q = data["quotes"][qi]
    li = lines.index { |ln| ln.start_with?("  - ") && NORM.(unwrap.(ln)) == NORM.(old_q) }
    unless li
      puts "FAIL(行定位失败) #{f} :: #{key[0, 20]}"
      next
    end
    lines[li, 1] = chosen.map { |q| "  - " + q.inspect + "\n" }
    File.write(path, lines.join)
    ok += 1
    puts "FIXED #{f} :: #{chosen.size}条 | #{chosen.first[0, 28]}…"
  end
end
puts "DONE fixed=#{ok}"
