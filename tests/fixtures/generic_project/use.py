from models import (Bag, Box, Conn, Flag, Holder, Item, Req, Req2, Sized, Tag, Weird, _cv, fetch, get_item, signal)


def loop_list(box: Box):
    for tag in box.tags:
        tag.check(1)                       # Tag.check (+ 자식 DictTag.check)


def dict_values(box: Box):
    for it in box.by_name.values():
        it.run()                           # Item.run


def dict_get(box: Box):
    box.by_name.get("a").run()             # Item.run
    box.by_name["a"].run()                 # Item.run


def items_loop(box: Box):
    for name, it in box.by_name.items():
        it.run()                           # Item.run
    for i, tg in enumerate(box.tags):
        tg.tag(i)                          # Tag.tag


def tuple_loop(box: Box):
    for it in box.pair:
        it.run()                           # tuple[Item, ...] -> Item.run


def sorted_loop(box: Box):
    for tg in sorted(box.tags):
        tg.tag(2)                          # sorted 는 원소 타입을 그대로 둔다


def ctxvar():
    _cv.get().run()                        # ContextVar[Item].get() -> Item.run
    if (y := _cv.get(None)) is not None:
        y.run()                            # walrus


def with_as():
    with Conn() as c:
        c.send()                           # __enter__ 가 self 를 돌려줌 -> Conn.send
    with Weird() as w:
        w.run()                            # __enter__ 가 5 를 돌려줌: Weird.run 이 아니다
    with open("f") as fh:
        fh.read()                          # 내장


def prop(h: Holder):
    h.item.run()                           # property 반환 힌트 -> Item.run


def ternary(flag):
    x = Item() if flag else Item()
    x.run()                                # 두 갈래가 같은 타입 -> Item.run
    y = Item() if flag else Tag()
    y.run()                                # 서로 다른 타입: 연결하지 않는다


async def awaited():
    r = await fetch()
    r.run()                                # await 한 값 -> Item.run
    r2 = fetch()
    r2.run()                               # await 없이는 코루틴이다: 연결하지 않는다


def overloaded():
    get_item().run()                       # @overload 는 정의로 세지 않는다 -> Item.run


def truth(s: Sized, f: Flag, it: Item):
    if s:
        pass                               # Sized.__len__
    if not f:
        pass                               # Flag.__bool__
    assert s
    if it:
        pass                               # Item 에는 __bool__/__len__ 이 없다
    if s is None:
        pass                               # 비교는 진리값 검사가 아니다


def bag(b: Bag):
    return b.get(1)


def externals(r: Req, r2: Req2):
    r.form.get("x")                        # form 은 프로젝트에서 정의하지 않았다: 외부 부모가 준 것
    r2.args.get("x")                       # 프로젝트가 정한 속성(타입 모름): 외부 부모 탓으로 돌리지 않는다
    signal.send()                          # 외부(fakelib) 객체
    d = r.__dict__.copy()                  # 어떤 객체의 __dict__ 든 dict
    return d
