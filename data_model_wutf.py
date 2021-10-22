from helper import *
from base.read import *
#from literal_encoder_wutf import *
from literal_encoder_without_assume import *
from utils import *
from sklearn import preprocessing


def save_literal_entity_vectors(p, literal_list,
                                literal_vectors, title):  # self.args.training_data, self.literal_entity_list, self.literal_entity_vectors_mat
    np.save('../file/' + p.dataset + '/' + title + '_vectors.npy', literal_vectors)  # LITERAL_entity_EMBEDDINGS_FILE = 'literal_entity_vectors.npy'
    assert len(literal_list) == literal_vectors.shape[0] # 61768
    with open('../file/' + p.dataset + '/' + title + '_literals.txt', 'w', encoding='utf-8') as file:  # LITERAL_FILE = 'literals.txt'
        for l in literal_list:
            file.write(l + '\n')
    #print('literals and embeddings are saved in', title + '_literals.txt')
    file.close()


def load_literal_entity_vectors(p, folder, entity_or_relation, title):  # 加载文字描述与文字向量
    print('load literal embeddings from', folder)
    literal_list = list()
    # mat = np.load(folder + entity_or_relation)  # LITERAL_EMBEDDINGS_FILE = 'literal_entity_vectors.npy'
    mat = np.load(entity_or_relation)
    # with open(folder + LITERAL_FILE, 'r', encoding='utf-8') as file:  # LITERAL_FILE = 'literals.txt'
    with open('../file/' + p.dataset + '/' + title + '_literals.txt', 'r', encoding='utf-8') as file:  # LITERAL_FILE = 'literals.txt'
        for line in file:
            line = line.strip('\n')
            literal_list.append(line)
    file.close()
    return literal_list, np.matrix(mat)


def generate_dict(literal_list, literal_vectors_list):  # 生成一个字典，将文字列表与文字向量表对应起来
    assert len(literal_list) == len(literal_vectors_list)
    dic = dict()
    list()
    for i in range(len(literal_list)):
        dic[literal_list[i]] = literal_vectors_list[i]
    return dic


def generate_literal_id_dic(literal_list):
    literal_id_dic = dict()
    for i in range(len(literal_list)):
        literal_id_dic[literal_list[i]] = i
    assert len(literal_list) == len(literal_id_dic)
    return literal_id_dic


class DataModel:
    def __init__(self, args, phr_list, phr2id, title, ordered=True):
        self.p = args
        self.phr_list = phr_list
        self.phr2id = phr2id
        self.title = title

        self.id2phr = invertDic(self.phr2id)
        self.word2vec_path = args.word2vec_path

        self._generate_literal_vectors()
        self._generate_name_vectors_mat()

    def _generate_literal_vectors(self):
        LITERAL_entity_EMBEDDINGS_FILE = '../file/' + self.p.dataset + '/literal_entity_vectors.npy'

        file_path = LITERAL_entity_EMBEDDINGS_FILE
        if not self.p.retrain_literal_embeds and os.path.exists(file_path):
            self.literal_entity_list, self.literal_entity_vectors_mat = load_literal_entity_vectors(self.p,
                self.p.training_data, LITERAL_entity_EMBEDDINGS_FILE, title=self.title)  # 'literal_entity_vectors.npy'
        else:
            word2vec = read_word2vec(self.word2vec_path)
            #word2vec = lower_word2vec(word2vec)
            literal_encoder = LiteralEncoder(self.p, word2vec, self.phr_list, self.phr2id, self.id2phr)
            self.literal_entity_vectors_mat = literal_encoder.encoded_literal_vector  # (61768, 300)

            literal_list = self.phr_list
            print(self.literal_entity_vectors_mat.shape[0])
            print(len(literal_list))
            #self.literal_id_list = literal_encoder.literal_id_list  # 61768，有ID的实体列表
            save_literal_entity_vectors(self.p, literal_list, self.literal_entity_vectors_mat, title=self.title)
            assert self.literal_entity_vectors_mat.shape[0] == len(literal_list)
        self.entity_id2id_dic = generate_literal_id_dic(self.phr_list)


    def _generate_name_vectors_mat(self):  # name view embeddings的生成，entity name
        entity_name_ordered_list = list()
        entities_num = len(self.entity_id2id_dic)
        print("total entities:", entities_num)
        self.id2entity_id_dic = invertDic(self.entity_id2id_dic)
        assert len(self.id2entity_id_dic) == entities_num

        for i in range(entities_num):  # 61768
            assert i in self.id2entity_id_dic
            literal_id = self.id2entity_id_dic.get(i)
            entity_name_index = self.entity_id2id_dic.get(literal_id)  # entity_name_index None
            entity_name_ordered_list.append(entity_name_index)
        entity_name_mat = self.literal_entity_vectors_mat[entity_name_ordered_list,]
        self.name_vectors = entity_name_mat
