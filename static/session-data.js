// Shared session data for all pages
const SESSION_DATA = {
  '二元一次方程': {
    questions: [
      { knowledge: '一元一次方程', question: '解方程：2x + 5 = 11，x 的值是多少？', options: ['x = 2', 'x = 3', 'x = 4', 'x = 8'], answer: 1 },
      { knowledge: '一元一次方程', question: '下列哪个是一元一次方程？', options: ['x² + 2x = 0', '3x + 2y = 7', '4x - 3 = 9', 'x² = 16'], answer: 2 },
      { knowledge: '二元一次方程组', question: '解方程组：x + y = 5，x - y = 1，则 x 的值为？', options: ['x = 2', 'x = 3', 'x = 4', 'x = 5'], answer: 1 },
      { knowledge: '二元一次方程组', question: '用代入法解方程组 y = 2x，x + y = 6，则 y 的值为？', options: ['y = 2', 'y = 3', 'y = 4', 'y = 6'], answer: 2 },
      { knowledge: '二元一次方程组', question: '方程组 2x + 3y = 12，x + 3y = 9 的解中，x 等于？', options: ['x = 1', 'x = 2', 'x = 3', 'x = 4'], answer: 2 },
      { knowledge: '不等式', question: '解不等式 3x - 2 > 7，x 的取值范围是？', options: ['x > 1', 'x > 2', 'x > 3', 'x > 5'], answer: 2 },
      { knowledge: '不等式', question: '下列不等式变换正确的是？', options: ['若 a > b，则 -a > -b', '若 a > b，则 a + c > b + c', '若 a > b，则 ac > bc', '若 a > b > 0，则 1/a > 1/b'], answer: 1 },
      { knowledge: '整式运算', question: '(2a²b)(3ab³) 的结果是？', options: ['5a²b³', '6a³b⁴', '6a²b³', '5a³b⁴'], answer: 1 },
      { knowledge: '整式运算', question: '(x + 2)(x - 3) 展开后的结果是？', options: ['x² - x - 6', 'x² + x - 6', 'x² - x + 6', 'x² - 5x - 6'], answer: 0 },
      { knowledge: '因式分解', question: 'x² - 9 可以分解为？', options: ['(x-3)(x-3)', '(x+3)(x+3)', '(x+3)(x-3)', '(x+9)(x-1)'], answer: 2 },
    ],
    results: {
      score: 70, total: 10, correct: 7, incorrect: 3,
      breakdown: [
        { name: '一元一次方程', status: 'mastered', detail: '2/2 已掌握' },
        { name: '二元一次方程组', status: 'weak', detail: '1/3 需复习' },
        { name: '不等式', status: 'weak', detail: '1/2 需复习' },
        { name: '整式运算', status: 'mastered', detail: '2/2 已掌握' },
        { name: '因式分解', status: 'skipped', detail: '已跳过' },
      ],
      weakPoints: '二元一次方程组 和 不等式',
      graphNodes: [
        { name: '一元一次方程', color: 'secondary', pos: 'top-[15%] left-[20%]' },
        { name: '二元一次方程组', color: 'error', pos: 'top-[15%] right-[20%]' },
        { name: '不等式', color: 'error', pos: 'bottom-[25%] left-[15%]' },
        { name: '整式运算', color: 'secondary', pos: 'bottom-[25%] right-[20%]' },
        { name: '因式分解', color: 'slate', pos: 'bottom-[50%] left-[10%]' },
      ]
    },
    lesson: {
      title: '二元一次方程：薄弱知识点',
      desc: '本教案根据你的诊断结果生成，重点讲解 <strong class="text-error">二元一次方程组</strong> 和 <strong class="text-error">不等式</strong>。',
      sections: [
        {
          id: 'section-1',
          name: '二元一次方程组',
          content: `
            <p>二元一次方程组是由两个含有两个未知数的一次方程组成的方程组。求解方程组就是找到一组 x、y 的值，使两个方程同时成立。</p>
            <h3 class="font-headline font-bold text-base text-on-surface mt-8 mb-3">核心概念</h3>
            <div class="bg-primary-fixed/30 rounded-2xl p-6 space-y-3">
              <p><strong>二元一次方程：</strong>含有两个未知数，且未知数的次数都是 1 的方程。一般形式为 ax + by = c。</p>
              <p><strong>方程组的解：</strong>使方程组中每个方程都成立的未知数的值。</p>
              <p><strong>解法：</strong>核心思想是"消元"——把二元问题转化为一元问题。</p>
            </div>
            <h3 class="font-headline font-bold text-base text-on-surface mt-8 mb-3">代入消元法</h3>
            <div class="bg-surface-container-low rounded-2xl p-6 border-l-4 border-primary">
              <p class="font-body text-sm text-on-surface-variant"><strong>例题：</strong>解方程组 y = 2x ①，x + y = 6 ②</p>
              <p class="font-body text-sm text-on-surface-variant mt-2">将①代入②：x + 2x = 6 → 3x = 6 → x = 2，y = 4</p>
            </div>
            <h3 class="font-headline font-bold text-base text-on-surface mt-8 mb-3">加减消元法</h3>
            <div class="bg-surface-container-low rounded-2xl p-6 border-l-4 border-primary">
              <p class="font-body text-sm text-on-surface-variant"><strong>例题：</strong>解方程组 2x + 3y = 12 ①，x + 3y = 9 ②</p>
              <p class="font-body text-sm text-on-surface-variant mt-2">① - ②：x = 3，代入②：y = 2</p>
            </div>
          `
        },
        {
          id: 'section-2',
          name: '不等式',
          content: `
            <p>不等式是用不等号连接的式子，表示两个量之间的大小关系。解不等式就是求出使不等式成立的未知数的取值范围。</p>
            <h3 class="font-headline font-bold text-base text-on-surface mt-8 mb-3">不等式的基本性质</h3>
            <div class="bg-primary-fixed/30 rounded-2xl p-6 space-y-3">
              <p><strong>性质 1：</strong>两边同加减同一个数，不等号方向不变。</p>
              <p><strong>性质 2：</strong>两边同乘正数，不等号方向不变。</p>
              <p><strong>性质 3：</strong>两边同乘负数，不等号方向<strong>改变</strong>。</p>
            </div>
            <h3 class="font-headline font-bold text-base text-on-surface mt-8 mb-3">常见易错点</h3>
            <div class="bg-surface-container-low rounded-2xl p-5">
              <p class="font-headline font-bold text-sm text-error mb-1">易错：乘负数忘记变号</p>
              <p class="text-sm text-on-surface-variant">例如 -2x > 6，两边除以 -2 后得 x < -3（不是 x > -3）。</p>
            </div>
          `
        }
      ],
      graphNodes: [
        { name: '方程组', id: 'section-1', pos: 'top-[18%] right-[10%]' },
        { name: '不等式', id: 'section-2', pos: 'bottom-[22%] left-[10%]' },
      ]
    }
  },

  '勾股定理': {
    questions: [
      { knowledge: '直角三角形', question: '直角三角形中，直角所对的边叫做？', options: ['斜边', '直角边', '底边', '腰'], answer: 0 },
      { knowledge: '直角三角形', question: '一个三角形的三个内角分别是 30°、60°、90°，它是？', options: ['等边三角形', '钝角三角形', '直角三角形', '等腰三角形'], answer: 2 },
      { knowledge: '勾股定理', question: '直角三角形两直角边分别为 3 和 4，斜边为？', options: ['5', '6', '7', '25'], answer: 0 },
      { knowledge: '勾股定理', question: '若直角三角形斜边为 13，一条直角边为 5，另一条直角边为？', options: ['8', '10', '12', '14'], answer: 2 },
      { knowledge: '勾股定理', question: '下列哪组数是勾股数？', options: ['2, 3, 4', '5, 12, 13', '4, 6, 8', '3, 5, 7'], answer: 1 },
      { knowledge: '平方与平方根', question: '√144 的值是？', options: ['11', '12', '13', '14'], answer: 1 },
      { knowledge: '平方与平方根', question: '下列哪个是无理数？', options: ['√4', '√9', '√5', '√16'], answer: 2 },
      { knowledge: '三角形面积', question: '底为 6，高为 4 的三角形面积是？', options: ['10', '12', '24', '20'], answer: 1 },
      { knowledge: '三角形面积', question: '等腰直角三角形的直角边为 5，面积是？', options: ['10', '12.5', '25', '15'], answer: 1 },
      { knowledge: '坐标距离', question: '点 A(1,2) 到点 B(4,6) 的距离是？', options: ['3', '4', '5', '7'], answer: 2 },
    ],
    results: {
      score: 80, total: 10, correct: 8, incorrect: 2,
      breakdown: [
        { name: '直角三角形', status: 'mastered', detail: '2/2 已掌握' },
        { name: '勾股定理', status: 'mastered', detail: '3/3 已掌握' },
        { name: '平方与平方根', status: 'weak', detail: '1/2 需复习' },
        { name: '三角形面积', status: 'mastered', detail: '2/2 已掌握' },
        { name: '坐标距离', status: 'weak', detail: '0/1 需复习' },
      ],
      weakPoints: '平方与平方根 和 坐标距离',
      graphNodes: [
        { name: '直角三角形', color: 'secondary', pos: 'top-[15%] left-[20%]' },
        { name: '勾股定理', color: 'secondary', pos: 'top-[15%] right-[20%]' },
        { name: '平方与平方根', color: 'error', pos: 'bottom-[25%] left-[15%]' },
        { name: '三角形面积', color: 'secondary', pos: 'bottom-[25%] right-[20%]' },
        { name: '坐标距离', color: 'error', pos: 'bottom-[50%] left-[10%]' },
      ]
    },
    lesson: {
      title: '勾股定理：薄弱知识点',
      desc: '本教案根据你的诊断结果生成，重点讲解 <strong class="text-error">平方与平方根</strong> 和 <strong class="text-error">坐标距离</strong>。',
      sections: [
        {
          id: 'section-1', name: '平方与平方根',
          content: `
            <p>平方根是平方运算的逆运算。如果 a² = b，则 a 是 b 的平方根，记作 √b = a。</p>
            <h3 class="font-headline font-bold text-base text-on-surface mt-8 mb-3">核心概念</h3>
            <div class="bg-primary-fixed/30 rounded-2xl p-6 space-y-3">
              <p><strong>完全平方数：</strong>能写成整数平方的数，如 1, 4, 9, 16, 25…</p>
              <p><strong>无理数：</strong>不能写成分数的数，如 √2, √3, √5，它们是无限不循环小数。</p>
              <p><strong>算术平方根：</strong>非负数 a 的算术平方根是非负的，记作 √a ≥ 0。</p>
            </div>
            <h3 class="font-headline font-bold text-base text-on-surface mt-8 mb-3">运算技巧</h3>
            <div class="bg-surface-container-low rounded-2xl p-6 border-l-4 border-primary">
              <p class="font-body text-sm text-on-surface-variant"><strong>例题：</strong>化简 √50</p>
              <p class="font-body text-sm text-on-surface-variant mt-2">√50 = √(25×2) = 5√2</p>
            </div>
          `
        },
        {
          id: 'section-2', name: '坐标距离',
          content: `
            <p>平面直角坐标系中，两点之间的距离可以用勾股定理推导得到距离公式。</p>
            <h3 class="font-headline font-bold text-base text-on-surface mt-8 mb-3">距离公式</h3>
            <div class="bg-primary-fixed/30 rounded-2xl p-6 space-y-3">
              <p><strong>公式：</strong>A(x₁, y₁) 与 B(x₂, y₂) 的距离 d = √[(x₂-x₁)² + (y₂-y₁)²]</p>
              <p><strong>本质：</strong>构造以 AB 为斜边的直角三角形，水平距离和垂直距离就是两条直角边，再用勾股定理。</p>
            </div>
            <h3 class="font-headline font-bold text-base text-on-surface mt-8 mb-3">例题</h3>
            <div class="bg-surface-container-low rounded-2xl p-6 border-l-4 border-primary">
              <p class="font-body text-sm text-on-surface-variant"><strong>例：</strong>A(1,2)，B(4,6)，求 AB 距离</p>
              <p class="font-body text-sm text-on-surface-variant mt-2">d = √[(4-1)² + (6-2)²] = √[9+16] = √25 = 5</p>
            </div>
          `
        }
      ],
      graphNodes: [
        { name: '平方根', id: 'section-1', pos: 'top-[18%] right-[10%]' },
        { name: '坐标距离', id: 'section-2', pos: 'bottom-[22%] left-[10%]' },
      ]
    }
  },

  '英语时态总结': {
    questions: [
      { knowledge: '一般现在时', question: 'She ___ to school every day.', options: ['go', 'goes', 'going', 'went'], answer: 1 },
      { knowledge: '一般现在时', question: '一般现在时的标志词是？', options: ['yesterday', 'now', 'usually', 'tomorrow'], answer: 2 },
      { knowledge: '一般过去时', question: 'I ___ a movie last night.', options: ['watch', 'watches', 'watched', 'watching'], answer: 2 },
      { knowledge: '一般过去时', question: '"go"的过去式是？', options: ['goed', 'went', 'gone', 'going'], answer: 1 },
      { knowledge: '现在进行时', question: 'Look! The children ___ in the park.', options: ['play', 'plays', 'are playing', 'played'], answer: 2 },
      { knowledge: '现在进行时', question: '现在进行时的结构是？', options: ['主语 + do', '主语 + did', '主语 + be + doing', '主语 + have done'], answer: 2 },
      { knowledge: '现在完成时', question: 'I ___ this book twice.', options: ['read', 'reads', 'have read', 'am reading'], answer: 2 },
      { knowledge: '现在完成时', question: '现在完成时常搭配的词是？', options: ['yesterday', 'now', 'already / yet', 'tomorrow'], answer: 2 },
      { knowledge: '一般现在时', question: 'Water ___ at 100°C.', options: ['boil', 'boils', 'is boiling', 'boiled'], answer: 1 },
      { knowledge: '一般过去时', question: 'They ___ football last Sunday.', options: ['play', 'playing', 'plays', 'played'], answer: 3 },
    ],
    results: {
      score: 60, total: 10, correct: 6, incorrect: 4,
      breakdown: [
        { name: '一般现在时', status: 'mastered', detail: '3/3 已掌握' },
        { name: '一般过去时', status: 'weak', detail: '1/3 需复习' },
        { name: '现在进行时', status: 'mastered', detail: '2/2 已掌握' },
        { name: '现在完成时', status: 'weak', detail: '0/2 需复习' },
      ],
      weakPoints: '一般过去时 和 现在完成时',
      graphNodes: [
        { name: '一般现在时', color: 'secondary', pos: 'top-[15%] left-[20%]' },
        { name: '一般过去时', color: 'error', pos: 'top-[15%] right-[20%]' },
        { name: '现在进行时', color: 'secondary', pos: 'bottom-[25%] left-[15%]' },
        { name: '现在完成时', color: 'error', pos: 'bottom-[25%] right-[20%]' },
      ]
    },
    lesson: {
      title: '英语时态：薄弱知识点',
      desc: '本教案根据你的诊断结果生成，重点讲解 <strong class="text-error">一般过去时</strong> 和 <strong class="text-error">现在完成时</strong>。',
      sections: [
        {
          id: 'section-1', name: '一般过去时',
          content: `
            <p>一般过去时表示过去某个时间发生的动作或存在的状态，常与 yesterday, last week, ago 等时间状语连用。</p>
            <h3 class="font-headline font-bold text-base text-on-surface mt-8 mb-3">构成规则</h3>
            <div class="bg-primary-fixed/30 rounded-2xl p-6 space-y-3">
              <p><strong>规则动词：</strong>动词原形 + ed（如 play → played，work → worked）</p>
              <p><strong>不规则动词：</strong>需要记忆（如 go → went，eat → ate，see → saw）</p>
              <p><strong>否定句：</strong>主语 + didn't + 动词原形</p>
            </div>
            <h3 class="font-headline font-bold text-base text-on-surface mt-8 mb-3">易错点</h3>
            <div class="bg-surface-container-low rounded-2xl p-5">
              <p class="font-headline font-bold text-sm text-error mb-1">否定句和疑问句中动词要还原</p>
              <p class="text-sm text-on-surface-variant">✗ He didn't went. ✓ He didn't go.（didn't 后用原形）</p>
            </div>
          `
        },
        {
          id: 'section-2', name: '现在完成时',
          content: `
            <p>现在完成时表示过去发生但对现在有影响的动作，或从过去持续到现在的状态。</p>
            <h3 class="font-headline font-bold text-base text-on-surface mt-8 mb-3">结构</h3>
            <div class="bg-primary-fixed/30 rounded-2xl p-6 space-y-3">
              <p><strong>构成：</strong>主语 + have/has + 过去分词（p.p.）</p>
              <p><strong>标志词：</strong>already, yet, ever, never, since, for, just</p>
            </div>
            <h3 class="font-headline font-bold text-base text-on-surface mt-8 mb-3">与一般过去时的区别</h3>
            <div class="bg-surface-container-low rounded-2xl p-6 border-l-4 border-primary">
              <p class="font-body text-sm text-on-surface-variant"><strong>过去时：</strong>I lost my key yesterday.（强调过去丢了）</p>
              <p class="font-body text-sm text-on-surface-variant mt-2"><strong>完成时：</strong>I have lost my key.（强调现在没钥匙）</p>
            </div>
          `
        }
      ],
      graphNodes: [
        { name: '过去时', id: 'section-1', pos: 'top-[18%] right-[10%]' },
        { name: '完成时', id: 'section-2', pos: 'bottom-[22%] left-[10%]' },
      ]
    }
  },

  '光的折射与反射': {
    questions: [
      { knowledge: '光的直线传播', question: '光在同一均匀介质中沿什么路线传播？', options: ['曲线', '直线', '折线', '抛物线'], answer: 1 },
      { knowledge: '光的直线传播', question: '影子的形成是因为？', options: ['光的反射', '光的折射', '光的直线传播', '光的色散'], answer: 2 },
      { knowledge: '入射角与反射角', question: '反射定律中，反射角等于？', options: ['折射角', '入射角', '直角', '零度'], answer: 1 },
      { knowledge: '入射角与反射角', question: '入射角是入射光线与什么之间的夹角？', options: ['反射面', '法线', '折射光线', '反射光线'], answer: 1 },
      { knowledge: '入射角与反射角', question: '镜面反射和漫反射都遵守反射定律吗？', options: ['是的', '只有镜面反射遵守', '只有漫反射遵守', '都不遵守'], answer: 0 },
      { knowledge: '折射率', question: '光从空气射入水中时，折射角比入射角？', options: ['大', '小', '相等', '不确定'], answer: 1 },
      { knowledge: '折射率', question: '折射率 n = 1.5 表示什么？', options: ['光速变为原来的 1.5 倍', '光在该介质中速度是真空中的 2/3', '折射角是入射角的 1.5 倍', '光的频率变为 1.5 倍'], answer: 1 },
      { knowledge: '全反射', question: '全反射发生在光从哪种介质射向哪种介质时？', options: ['从光疏到光密', '从光密到光疏', '任何情况', '只在真空中'], answer: 1 },
      { knowledge: '全反射', question: '光纤通信利用了什么原理？', options: ['光的反射', '光的色散', '全反射', '光的干涉'], answer: 2 },
      { knowledge: '折射率', question: '光从水射入空气时，光的速度会？', options: ['变慢', '变快', '不变', '变为零'], answer: 1 },
    ],
    results: {
      score: 50, total: 10, correct: 5, incorrect: 5,
      breakdown: [
        { name: '光的直线传播', status: 'mastered', detail: '2/2 已掌握' },
        { name: '入射角与反射角', status: 'weak', detail: '1/3 需复习' },
        { name: '折射率', status: 'weak', detail: '1/3 需复习' },
        { name: '全反射', status: 'weak', detail: '1/2 需复习' },
      ],
      weakPoints: '入射角与反射角、折射率 和 全反射',
      graphNodes: [
        { name: '光的直线传播', color: 'secondary', pos: 'top-[15%] left-[20%]' },
        { name: '入射角与反射角', color: 'error', pos: 'top-[15%] right-[15%]' },
        { name: '折射率', color: 'error', pos: 'bottom-[25%] left-[15%]' },
        { name: '全反射', color: 'error', pos: 'bottom-[25%] right-[20%]' },
      ]
    },
    lesson: {
      title: '光的折射与反射：薄弱知识点',
      desc: '本教案根据你的诊断结果生成，重点讲解 <strong class="text-error">反射定律</strong>、<strong class="text-error">折射率</strong> 和 <strong class="text-error">全反射</strong>。',
      sections: [
        {
          id: 'section-1', name: '入射角与反射角',
          content: `
            <p>反射定律是光学的基本定律之一，描述了光在平面上发生反射时入射光线、反射光线和法线之间的关系。</p>
            <h3 class="font-headline font-bold text-base text-on-surface mt-8 mb-3">反射定律三要素</h3>
            <div class="bg-primary-fixed/30 rounded-2xl p-6 space-y-3">
              <p><strong>1. 三线共面：</strong>入射光线、反射光线和法线在同一平面内。</p>
              <p><strong>2. 两线分居：</strong>入射光线和反射光线分居法线两侧。</p>
              <p><strong>3. 两角相等：</strong>反射角等于入射角。</p>
            </div>
          `
        },
        {
          id: 'section-2', name: '折射率',
          content: `
            <p>折射率描述了光在不同介质中传播速度的差异，是理解折射现象的关键。</p>
            <h3 class="font-headline font-bold text-base text-on-surface mt-8 mb-3">斯涅尔定律</h3>
            <div class="bg-primary-fixed/30 rounded-2xl p-6 space-y-3">
              <p><strong>公式：</strong>n₁ sin θ₁ = n₂ sin θ₂</p>
              <p><strong>折射率：</strong>n = c / v，其中 c 是真空光速，v 是介质中光速。</p>
            </div>
            <div class="bg-surface-container-low rounded-2xl p-6 border-l-4 border-primary mt-4">
              <p class="font-body text-sm text-on-surface-variant"><strong>例：</strong>水的折射率 n=1.33，说明光在水中的速度约为真空中的 75%。</p>
            </div>
          `
        },
        {
          id: 'section-3', name: '全反射',
          content: `
            <p>当光从光密介质（折射率大）射向光疏介质（折射率小）时，入射角超过临界角就会发生全反射。</p>
            <h3 class="font-headline font-bold text-base text-on-surface mt-8 mb-3">条件</h3>
            <div class="bg-primary-fixed/30 rounded-2xl p-6 space-y-3">
              <p><strong>条件一：</strong>光从光密介质射向光疏介质。</p>
              <p><strong>条件二：</strong>入射角 ≥ 临界角。</p>
              <p><strong>应用：</strong>光纤通信、钻石闪耀、海市蜃楼。</p>
            </div>
          `
        }
      ],
      graphNodes: [
        { name: '反射角', id: 'section-1', pos: 'top-[15%] right-[10%]' },
        { name: '折射率', id: 'section-2', pos: 'bottom-[35%] left-[10%]' },
        { name: '全反射', id: 'section-3', pos: 'bottom-[15%] right-[10%]' },
      ]
    }
  },

  '细胞的结构与功能': {
    questions: [
      { knowledge: '细胞膜', question: '细胞膜的主要成分是？', options: ['蛋白质和核酸', '磷脂双分子层和蛋白质', '糖类和脂肪', '水和无机盐'], answer: 1 },
      { knowledge: '细胞膜', question: '细胞膜的功能特性是？', options: ['流动性', '选择透过性', '全透性', '不透性'], answer: 1 },
      { knowledge: '细胞核', question: '细胞核的主要功能是？', options: ['合成蛋白质', '储存遗传信息并控制细胞活动', '提供能量', '进行光合作用'], answer: 1 },
      { knowledge: '细胞核', question: 'DNA 主要存在于细胞的哪个结构中？', options: ['细胞膜', '细胞质', '细胞核', '液泡'], answer: 2 },
      { knowledge: '细胞核', question: '没有细胞核的细胞是？', options: ['神经细胞', '成熟红细胞', '白细胞', '肌肉细胞'], answer: 1 },
      { knowledge: '线粒体', question: '线粒体被称为细胞的？', options: ['控制中心', '动力工厂', '蛋白质加工厂', '消化车间'], answer: 1 },
      { knowledge: '线粒体', question: '线粒体进行的是什么生理过程？', options: ['光合作用', '有氧呼吸', '无氧呼吸', 'DNA复制'], answer: 1 },
      { knowledge: '内质网', question: '粗面内质网上附着的是？', options: ['线粒体', '叶绿体', '核糖体', '溶酶体'], answer: 2 },
      { knowledge: '内质网', question: '滑面内质网的主要功能是？', options: ['合成蛋白质', '合成脂质', '储存遗传信息', '进行光合作用'], answer: 1 },
      { knowledge: '线粒体', question: '线粒体有几层膜？', options: ['0层', '1层', '2层', '3层'], answer: 2 },
    ],
    results: {
      score: 90, total: 10, correct: 9, incorrect: 1,
      breakdown: [
        { name: '细胞膜', status: 'mastered', detail: '2/2 已掌握' },
        { name: '细胞核', status: 'mastered', detail: '3/3 已掌握' },
        { name: '线粒体', status: 'mastered', detail: '3/3 已掌握' },
        { name: '内质网', status: 'weak', detail: '1/2 需复习' },
      ],
      weakPoints: '内质网',
      graphNodes: [
        { name: '细胞膜', color: 'secondary', pos: 'top-[15%] left-[20%]' },
        { name: '细胞核', color: 'secondary', pos: 'top-[15%] right-[20%]' },
        { name: '线粒体', color: 'secondary', pos: 'bottom-[25%] left-[15%]' },
        { name: '内质网', color: 'error', pos: 'bottom-[25%] right-[20%]' },
      ]
    },
    lesson: {
      title: '细胞的结构与功能：薄弱知识点',
      desc: '本教案根据你的诊断结果生成，重点讲解 <strong class="text-error">内质网</strong>。',
      sections: [
        {
          id: 'section-1', name: '内质网',
          content: `
            <p>内质网是细胞质中由膜构成的网状管道系统，是细胞内蛋白质和脂质合成与运输的重要场所。</p>
            <h3 class="font-headline font-bold text-base text-on-surface mt-8 mb-3">两种类型</h3>
            <div class="bg-primary-fixed/30 rounded-2xl p-6 space-y-3">
              <p><strong>粗面内质网（RER）：</strong>表面附着核糖体，负责合成分泌蛋白，并对蛋白质进行初步加工（折叠、糖基化）。</p>
              <p><strong>滑面内质网（SER）：</strong>表面光滑，负责合成脂质（如磷脂、固醇），也参与糖原代谢和解毒。</p>
            </div>
            <h3 class="font-headline font-bold text-base text-on-surface mt-8 mb-3">在分泌蛋白合成中的角色</h3>
            <div class="bg-surface-container-low rounded-2xl p-6 border-l-4 border-primary">
              <p class="font-body text-sm text-on-surface-variant"><strong>路径：</strong>核糖体合成肽链 → 粗面内质网（加工、运输）→ 高尔基体（进一步加工、分拣）→ 细胞膜（分泌出细胞）</p>
            </div>
            <h3 class="font-headline font-bold text-base text-on-surface mt-8 mb-3">与其他细胞器的关系</h3>
            <ul class="list-disc list-inside space-y-2 text-on-surface-variant">
              <li>与核膜相连，便于接收来自细胞核的指令（mRNA）</li>
              <li>通过出芽形成转运囊泡，将蛋白质运往高尔基体</li>
              <li>滑面内质网合成的磷脂可补充各种生物膜</li>
            </ul>
          `
        }
      ],
      graphNodes: [
        { name: '内质网', id: 'section-1', pos: 'top-[30%] right-[10%]' },
      ]
    }
  }
};

// Helper: get current session name
function getCurrentSessionName() {
  try {
    const sessions = JSON.parse(localStorage.getItem('openail_sessions') || '[]');
    const currentId = localStorage.getItem('openail_current_session');
    const current = sessions.find(s => s.id === currentId);
    return current ? current.name : '二元一次方程';
  } catch { return '二元一次方程'; }
}

function getSessionData() {
  const name = getCurrentSessionName();
  return SESSION_DATA[name] || SESSION_DATA['二元一次方程'];
}
