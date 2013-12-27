#include <stdio.h>
#include <stdlib.h>
#include <limits.h>

typedef unsigned char uint8;
typedef unsigned int uint32;

uint8 *snes_compress(uint8 *data,int data_size, int *compressed_size);

uint8 reverse_bits(uint8 b);
int main(int argc,char *argv[]){
    if(argc < 2){
        printf("Usage: snescompress <infilename> <outfilename>\n");
        return 0;
    }

    char *infilename, *outfilename;
    infilename = argv[1];

    //open the input file pointer
    FILE *infp = fopen(infilename,"rb");

    //get the size of the input file
    fseek(infp,0,SEEK_END);
    int infilesize = ftell(infp);
    fseek(infp,0,SEEK_SET);

    //allocate array and place entire input file in memory
    uint8 *infile_data=(uint8*)malloc(infilesize);
    fread(infile_data,infilesize,1,infp);
    fclose(infp);

    //int i;
    //for(i = 0;i < infilesize;i++){
    //    printf("%02x ",infile_data[i]);
    //    if(i%16 == 0)printf("\n");
    //}
    //printf("\n");

    int compressed_size;
    uint8 *outfile_data = snes_compress(infile_data,infilesize,&compressed_size);

    if(argc >= 3){
        //write compressed output
        outfilename = argv[2];
        FILE *outfp = fopen(outfilename,"wb");
        fwrite(outfile_data,compressed_size,1,outfp);
        fclose(outfp);
    }

    free(outfile_data);
    free(infile_data);
    return 0;
}


//we use different bits in the status flag
#define STATUS_LEAF     0x1
#define STATUS_DELETEME 0x2
#define STATUS_CHECKED  0x4

#define LZ_TYPE_NORMAL      0
#define LZ_TYPE_BITREVERSED 1
#define LZ_TYPE_REVERSED    2

typedef struct _compress_node {
    uint32 type;
    uint32 status;
    uint32 uncompressed_size;
    uint32 compressed_size;

//    struct _compress_node *prev;
    struct _compress_node *next;
    struct _compress_node *parent;
} compress_node;

typedef struct {
    compress_node *head;
    uint32 num_nodes;
    uint8 *data;
    uint32 data_size;
} compress_tree;


int node_size_comparison(const void *p1,const void *p2);

int get_compression_uncompressed(compress_tree *tree, compress_node *node, int *uncompressed_size,int *compressed_size);
int get_compression_rle(compress_tree *tree, compress_node *node, int *uncompressed_size,int *compressed_size);
int get_compression_2byte_rle(compress_tree *tree, compress_node *node, int *uncompressed_size,int *compressed_size);
int get_compression_rle_increment(compress_tree *tree, compress_node *node, int *uncompressed_size,int *compressed_size);
int get_compression_lz(compress_tree *tree, compress_node *node, int *uncompressed_size,int *compressed_size);
int get_compression_lz_bitreversed(compress_tree *tree, compress_node *node, int *uncompressed_size,int *compressed_size);
int get_compression_lz_reversed(compress_tree *tree, compress_node *node, int *uncompressed_size,int *compressed_size);
int get_compression_invalid(compress_tree *tree, compress_node *node, int *uncompressed_size,int *compressed_size);

int do_lz_compress(compress_tree *tree, compress_node *node, int *uncompressed_size,int *compressed_size,int *lz_location,int *lz_length,int lz_type);

int output_compression_uncompressed(compress_tree *tree, compress_node *node,uint8 *compressed_buf);
int output_compression_rle(compress_tree *tree, compress_node *node,uint8 *compressed_buf);
int output_compression_2byte_rle(compress_tree *tree, compress_node *node,uint8 *compressed_buf);
int output_compression_rle_increment(compress_tree *tree, compress_node *node,uint8 *compressed_buf);
int output_compression_lz(compress_tree *tree, compress_node *node,uint8 *compressed_buf);
int output_compression_lz_bitreversed(compress_tree *tree, compress_node *node,uint8 *compressed_buf);
int output_compression_lz_reversed(compress_tree *tree, compress_node *node,uint8 *compressed_buf);

uint8 *snes_compress(uint8 *data,int data_size, int *compressed_size){

    compress_tree *tree = (compress_tree *)malloc(sizeof(compress_tree));
    
    //set up our array of compression type pointers for Kirby
    int (*get_compression[7])(compress_tree *tree, compress_node *node, int *uncompressed_size,int *compressed_size);

    get_compression[0] = get_compression_uncompressed;
    get_compression[1] = get_compression_rle;
    get_compression[2] = get_compression_2byte_rle;
    get_compression[3] = get_compression_rle_increment;
    get_compression[4] = get_compression_lz;
    get_compression[5] = get_compression_lz_bitreversed;
    get_compression[6] = get_compression_lz_reversed;

    int (*output_compression[7])(compress_tree *tree, compress_node *node,uint8 *compressed_buf);
    output_compression[0] = output_compression_uncompressed;
    output_compression[1] = output_compression_rle;
    output_compression[2] = output_compression_2byte_rle;
    output_compression[3] = output_compression_rle_increment;
    output_compression[4] = output_compression_lz;
    output_compression[5] = output_compression_lz_bitreversed;
    output_compression[6] = output_compression_lz_reversed;

    tree->data = data;
    tree->data_size = data_size;
    tree->num_nodes = 1;
    
    tree->head = (compress_node *)malloc(sizeof(compress_node));
    tree->head->type = -1; //-1 is an identifier for the HEAD node
    tree->head->status = STATUS_LEAF;
    tree->head->uncompressed_size = tree->head->compressed_size = 0;
    tree->head->next = tree->head->parent = NULL;


    int i;
    while(1){
        int leaf_node_count = 0;
        int progress=0;

        //go through node, making children for leaf nodes.
        compress_node *p = tree->head;
        while(p != NULL){
            //only make children for nodes that are currently leaf nodes
            //and for nodes that have not reached the end of the uncompressed stream
            if(!(p->status&STATUS_LEAF) || p->uncompressed_size >= tree->data_size){
                p = p->next;
                continue;
            }

            leaf_node_count++;

            for(i = 0;i < 7;i++){
                int u_size, c_size;
                
                int is_valid=(*get_compression[i])(tree,p,&u_size,&c_size);
                
                //printf("inputlocation: %d type: %d valid: %d sample: %02x%02x\n\n"
                //,p->uncompressed_size,i,is_valid,
                //tree->data[p->uncompressed_size],tree->data[p->uncompressed_size+1]);

                //this compression type is valid... add it to our tree
                if(is_valid){
                    if(progress < u_size)progress = u_size;
                    compress_node *newnode = (compress_node *)malloc(sizeof(compress_node));

                    //the type of compression for the new node will be the type we checked for
                    newnode->type = i;

                    //if we haven't reached the end of the input stream then our new node will be a leaf node
                    if(u_size < tree->data_size);
                        newnode->status = STATUS_LEAF;

                    //set compressed and uncompressed size to the value that we got from get_compression
                    newnode->uncompressed_size = u_size;
                    newnode->compressed_size = c_size;

                    //set the new node's parent to p
                    newnode->parent = p;

                    //insert the new node in the list
                    newnode->next = tree->head;
                    tree->head = newnode;
                    tree->num_nodes++;
                 }
            }
            //this node may have had children. It is no longer a leaf node.
            p->status = p->status & (~STATUS_LEAF);
            p = p->next;
        }


        //PHASE 2: prune the tree
        //Step 1: Put pointers to all nodes in an array so we can sort them.
        compress_node **node_pointer_array = (compress_node **)malloc(sizeof(compress_node *)*(tree->num_nodes));

        p = tree->head;
        i=0;

        while(p != NULL){
            node_pointer_array[i++] = p;

            //go ahead and set every node's status to unchecked here.
            p->status &= ~STATUS_CHECKED;
            p = p->next;
        }

        //Sort the array using c built in sort function
        //we want to sort by uncompressed size in descending order.
        qsort(node_pointer_array,tree->num_nodes,sizeof(compress_node *),node_size_comparison);
            
        //loop through the array, deleting any nodes that have fallen behind
        compress_node **node_iterator;
        int compressed_size_tracker = (*node_pointer_array)->compressed_size;
        int uncompressed_size_tracker = (*node_pointer_array)->uncompressed_size;

        for(node_iterator = (node_pointer_array+1);node_iterator < (node_pointer_array+tree->num_nodes);node_iterator++){
            int c_size = (*node_iterator)->compressed_size;
            int u_size = (*node_iterator)->uncompressed_size;

            //type 0 is special... It is possible that we could be gaining an extra byte with that type.
            //Assume that it may be saving an extra byte when doing the prune test.
            int compressed_compare_size = c_size;
            if((*node_iterator)->type == 0)compressed_compare_size--;
                
            if(u_size == uncompressed_size_tracker && compressed_compare_size >= compressed_size_tracker)
                (*node_iterator)->status |= STATUS_DELETEME;
            
            compressed_size_tracker = c_size;
            uncompressed_size_tracker = u_size;
        }

        //node_pointer_array has served it's purpose for now.
        free(node_pointer_array);

        //Find all children of nodes we want to delete and delete them as well.

        //check every node
        p = tree->head;
        while(p != NULL){
            //all nodes are either children of a deleted node, or children of the head node.
            //go up the tree, until we reach a deleted node or another checked node.
            int found_deleted_node = 0;

            compress_node *p_tree = p;
            while(p_tree != NULL){
                //note that deleted nodes have checked status as well, thus the order of these if statements matters
                if(p_tree->status&STATUS_DELETEME){ //found a deleted node
                    found_deleted_node = 1;
                    break;
                }
                if(p_tree->status&STATUS_CHECKED){ //found a checked node that wasn't deleted
                    break;
                }
                p_tree = p_tree->parent;
            }

            //walk up the tree again, this time set everything to checked,
            //if we reached a deleted node last time, then set every child to deleted
            p_tree = p;
            while(p_tree != NULL){
                //stop walking when we reach a checked or deleted node.
                if(p_tree->status&STATUS_CHECKED || p_tree->status&STATUS_DELETEME)
                    break;

                //set the current node to checked
                p_tree->status |= STATUS_CHECKED;
                if(found_deleted_node){
                    //if we found a deleted node last time, this will set all of it's children to deleted
                    p_tree->status |= STATUS_DELETEME;
                }
                p_tree = p_tree->parent;
            }

            p=p->next;
        }

        //Delete the nodes that were flagged for deletion
        int deleted_node_count=0;        
        compress_node **p_prev = &(tree->head);
        while(*p_prev != NULL){
            if((*p_prev)->status&STATUS_DELETEME){
                compress_node *p_tmp = (*p_prev)->next;
                free(*p_prev);
                tree->num_nodes--;
                deleted_node_count++;
                *p_prev = p_tmp;
            } else {
                p_prev = &((*p_prev)->next);
            }
        }
        printf("leaf_count: %d deleted_count %d total nodes: %d progress: %d\n",
        leaf_node_count,deleted_node_count,tree->num_nodes,progress);
        
        //if we encountered no leaf nodes, then we are done
        if(leaf_node_count == 0)
            break;
    }

    //if we made it here, it means we have a tree with no leaf nodes. One or more optimal
    //compression sequences will be in this tree. Find the first one.

    compress_node *p = tree->head;
    int best_match_size=INT_MAX;
    compress_node *p_best=NULL;
    while(p != NULL){
        if(p->uncompressed_size == tree->data_size){
            if(best_match_size > p->compressed_size){
                best_match_size = p->compressed_size;
                p_best = p;
            }
        }
        p=p->next;
    }

    //this indicates a serious bug in the program, it means that there isn't an optimal p in the tree.
    if(p_best == NULL){
        printf("Serious bug encountered.\n");
        return;
    }

    uint8 *compressed_data = (uint8 *)malloc(p_best->compressed_size+1);
    compress_node *p_walk=p_best;

    int node_count=0;
    while(p_walk != NULL){
        int type = p_walk->type;
        if(type < 0)break;
        int c_size=output_compression[type](tree,p_walk,compressed_data);
//        printf("c->type %d c->parent->type %d c->c_size %d\n",p_walk->type,p_walk->parent->type,p_walk->compressed_size);
        printf("type: %d\n",type);        
        printf("Compressed: ");
        for(i = 0;i < c_size;i++){
            printf("%02X",compressed_data[p_walk->parent->compressed_size+i]);
        }

        printf("\nUncompressed: ");
        for(i = p_walk->parent->uncompressed_size;i < p_walk->uncompressed_size;i++){
            printf("%02X",tree->data[i]);
        }
        printf("\n");

        if(c_size != p_walk->compressed_size-p_walk->parent->compressed_size){
            printf("ERROR! Serious Bug Encountered. csize=%d compressed_size=%d parent->compressed_size=%d\n",
            c_size,p_walk->compressed_size,p_walk->parent->compressed_size);
            return compressed_data;
        }
        node_count++;
        p_walk = p_walk->parent;
    }
    compressed_data[p_best->compressed_size] = 0xff;

    *compressed_size = p_best->compressed_size+1;

    printf("Optimal compression size: %d Uncompressed size %d\n",*compressed_size,tree->data_size);


    //TODO: CLEANUP!!!
    return compressed_data;
}

int node_size_comparison(const void *p1,const void *p2){
    compress_node **p1_cast = (compress_node **)p1;
    compress_node **p2_cast = (compress_node **)p2;
    int p1_uncompressed = (*p1_cast)->uncompressed_size;
    int p1_compressed = (*p1_cast)->compressed_size;
    int p2_uncompressed = (*p2_cast)->uncompressed_size;
    int p2_compressed = (*p2_cast)->compressed_size;

    //Type 0 is special case. It is possible that we could be gaining an extra byte with that type.
    //Do the sort assuming that we are.
    if((*p1_cast)->type == 0)p1_compressed--;
    if((*p2_cast)->type == 0)p2_compressed--;

    //sort by uncompressed size in descending order.
    if(p1_uncompressed < p2_uncompressed)
        return 1;

    //if uncompressed size is same, sort by compressed size in ascending order
    if(p1_uncompressed == p2_uncompressed){
        if(p1_compressed < p2_compressed){
            return -1;
        }
        return 1;
    }

    return -1;
}

//get_compression_***() parameters:
//
//compress_tree *tree    - pointer to tree struct
//compress_node *node    - pointer to the node that we are compressing from
//int *uncompressed_size - points to an int that will be set to the uncompressed size
//                         if we used this compression technique
//                         NOTE: This is NOT initialized. The function is responsible for setting it.
//int *compressed_size   - pointer to an int that will bte set to the compressed size
//                         if we use this compression technique
//                         NOTE: This is NOT initialized. The function is responsible for setting it.
//
//returns int - If this is set to 0 then we can not use this compression technique.
//              compressed_size and uncompressed_size will be unmodified in this case.

int get_compression_uncompressed(compress_tree *tree, compress_node *node, int *uncompressed_size,int *compressed_size){
    *compressed_size = node->compressed_size;

    compress_node *p=node;
    int i=0;
    while(p != NULL && p->type == 0){
        i++;
        p=p->parent;
    }
    //if there are 31 parents that are all uncompressed, then we will need to account for the extra length byte
    //that it takes to have an uncompressed block of 
    if(i == 32)(*compressed_size)++;

    //if there are a multiple of 1024 parents all uncompressed, then we need to start a new block.
    //This means we need an extra byte.
    if(i%1024 == 1023)(*compressed_size)++;

    //if parent's type is also uncompressed, then the length is 1 byte larger
    if(node->type == 0){
        (*compressed_size)++;
    } else {
    //if the parent's type is another type then the lengh is 2 bytes to account for the header
        (*compressed_size)+=2;
    }

    *uncompressed_size = node->uncompressed_size + 1;
    return 1;
}


int get_compression_rle(compress_tree *tree, compress_node *node, int *uncompressed_size,int *compressed_size){
    int rle_count=0;
    int i;
    for(i = node->uncompressed_size+1;i < tree->data_size;i++){
        if(tree->data[i] == tree->data[i-1])rle_count++;
        else break;
    }

    //if there is nothing here to compress using rle
    if(rle_count == 0)return 0;

    //rle_count+1 tells the number of consecutive matching bytes
    //the length attribute in the header will be rle_count.
    //If this exceeds 31 then we need a 2 byte header.
    if(rle_count > 31)
        //2 bytes for header + 1 byte to rle
        *compressed_size = node->compressed_size + 2 + 1;
    else
        //1 byte for header + 1 byte to rle
        *compressed_size = node->compressed_size + 1 + 1;

    *uncompressed_size = node->uncompressed_size + rle_count+1;
    return 1;
}

int get_compression_2byte_rle(compress_tree *tree, compress_node *node, int *uncompressed_size,int *compressed_size){
    int rle_count=0;
    int i;
    for(i = node->uncompressed_size+3;i < tree->data_size;i+=2){
        if((tree->data[i] == tree->data[i-2]) &&
            (tree->data[i-1] == tree->data[i-3]))rle_count++;
        else break;
    }

    //if there is nothing here to compress, return invalid
    if(rle_count == 0)return 0;

    //rle_count+1 tells the number of consecutive matching bytes*2
    //the length attribute in the header will be rle_count.
    //If this exceeds 31 then we need a 2 byte header.
    if(rle_count > 31)
        //2 bytes for header + 2 bytes to rle
        *compressed_size = node->compressed_size + 2 + 2;
    else
        //1 byte for header + 2 bytes to rle
        *compressed_size = node->compressed_size + 1 + 2;

    *uncompressed_size = node->uncompressed_size + ((rle_count+1)*2);
    return 1;
}

int get_compression_rle_increment(compress_tree *tree, compress_node *node, int *uncompressed_size,int *compressed_size){
    int rle_count=0;
    int i;
    for(i = node->uncompressed_size+1;i < tree->data_size;i++){
        if(tree->data[i] == (tree->data[i-1]+1))rle_count++;
        else break;
    }

    //if there is nothing here to compress using rle
    if(rle_count == 0)return 0;

    //rle_count+1 tells the number of consecutive matching bytes
    //the length attribute in the header will be rle_count.
    //If this exceeds 31 then we need a 2 byte header.
    if(rle_count > 31)
        //2 bytes for header + 1 byte to rle
        *compressed_size = node->compressed_size + 2 + 1;
    else
        //1 byte for header + 1 byte to rle
        *compressed_size = node->compressed_size + 1 + 1;

    *uncompressed_size = node->uncompressed_size + rle_count+1;
    return 1;
}

int get_compression_lz(compress_tree *tree, compress_node *node, int *uncompressed_size,int *compressed_size){
    return do_lz_compress(tree,node,uncompressed_size,compressed_size,NULL,NULL,LZ_TYPE_NORMAL);
}


int get_compression_lz_bitreversed(compress_tree *tree, compress_node *node, int *uncompressed_size,int *compressed_size){
    return do_lz_compress(tree,node,uncompressed_size,compressed_size,NULL,NULL,LZ_TYPE_BITREVERSED);
}

int get_compression_lz_reversed(compress_tree *tree, compress_node *node, int *uncompressed_size,int *compressed_size){
    return do_lz_compress(tree,node,uncompressed_size,compressed_size,NULL,NULL,LZ_TYPE_REVERSED);
}


int get_compression_invalid(compress_tree *tree, compress_node *node, int *uncompressed_size,int *compressed_size){
    return 0;
}


uint8 reverse_bits(uint8 b){
    int i;
    uint8 r=0;
    for(i = 0;i < 8;i++){
        r<<=1;
        if(b&1)r|=0x01;
        b>>=1;
    }
    return r;
}

int do_lz_compress(compress_tree *tree, compress_node *node, int *uncompressed_size,int *compressed_size,int *lz_location,int *lz_length,int lz_type){
    int lz_count=0;
    int i, j;

    //the lz in this compression format uses an absolute address, as such it can't exceed 2 bytes
    //or 64KB of addressable memory
    int maxlen = tree->data_size < 65536 ? tree->data_size : 65536;
    int max_uncompressed = node->uncompressed_size < 65536 ? node->uncompressed_size : 65536;
    //search through input buffer for longest matching string
    int best_lz_match=0;
    int match_location=0;

    //implement different types of LZ here
    if(lz_type == LZ_TYPE_NORMAL){
        for(i = 0;i < max_uncompressed;i++){
            //check for matches beginning here
            int lz_match=0;
            for(j = 0;max_uncompressed+j < maxlen;j++){
                if(tree->data[i+j] == tree->data[max_uncompressed+j]){
                    lz_match++;
                } else break;
            }
            if(lz_match > best_lz_match){
                best_lz_match = lz_match;
                match_location=i;
            }
        }
    } else if(lz_type == LZ_TYPE_BITREVERSED){
        for(i = 0;i < max_uncompressed;i++){
            //check for matches beginning here
            int lz_match=0;
            for(j = 0;max_uncompressed+j < maxlen;j++){
                if(reverse_bits(tree->data[i+j]) == tree->data[max_uncompressed+j]){
                    lz_match++;
                } else break;
            }
            if(lz_match > best_lz_match){
                best_lz_match = lz_match;
                match_location=i;
            }
        }
    } else if(lz_type == LZ_TYPE_REVERSED){
        for(i = 0;i < max_uncompressed;i++){
            int lz_match=0;
            //check in reverse order
            for(j = 0;j+i > 0;j--){
                int k = -j;
                if(max_uncompressed+k >= maxlen)break; //don't allow writing past end
                if(tree->data[i+j] == tree->data[max_uncompressed+k]){
                    lz_match++;
                } else break;
            }
            if(lz_match > best_lz_match){
                best_lz_match = lz_match;
                match_location=i;
            }
        }
    }

    //There should be at least a 3 byte match for lz to be worth it, because it takes
    //2 bytes to give an address, and we could have simply output raw data
    if(best_lz_match < 3)return 0;

    //save the match location if applicable
    if(lz_location != NULL)
        *lz_location = match_location;
    if(lz_length != NULL)
        *lz_length = best_lz_match-1;

    //best_lz_match tells the number of consecutive matching bytes
    //the length attribute in the header will be best_lz_match-1.
    //If this exceeds 31 then we need a 2 byte header.
    if(best_lz_match > 32)
        //2 bytes for header + 2 byte lz address
        *compressed_size = node->compressed_size + 2 + 2;
    else
        //1 byte for header + 2 byte lz address
        *compressed_size = node->compressed_size + 1 + 2;

    *uncompressed_size = node->uncompressed_size + best_lz_match;
    return 1;
}


int output_compression_uncompressed(compress_tree *tree, compress_node *node,uint8 *compressed_buf){
    int type=node->type;

    //walk up the tree until we hit something that ISN'T uncompressed
    //we collapse the tree and remove extra uncompressed elements that are now unnecessary.
    int data_len=0;
    compress_node *p=node;

    while(p->parent != NULL && p->parent->type == 0){
        data_len++;
        compress_node *p_tmp = p->parent->parent;
        free(p->parent);
        p->parent = p_tmp;
    }
    int output_location = p->parent->compressed_size;

    int output_size;
    if(data_len < 32){
        compressed_buf[output_location++] = data_len|(type<<5);
        output_size=1;
    }else{
        compressed_buf[output_location++] = 0xE0 | type<<2 | (data_len)>>8;
        compressed_buf[output_location++] = (data_len)&0xFF;
        output_size=2;
    }
    int i;
    int uncompressed_location=node->uncompressed_size-data_len-1;

    for(i = 0;i < data_len+1;i++){
        compressed_buf[output_location++] = tree->data[uncompressed_location++];
    }

    output_size +=data_len+1;
    return output_size;

}

int output_compression_rle(compress_tree *tree, compress_node *node,uint8 *compressed_buf){
    int type=node->type;
    int uncompressed_location = node->parent->uncompressed_size;
    int output_location = node->parent->compressed_size;

    int rle_count=0;
    int i;
    for(i = uncompressed_location+1;i < tree->data_size;i++){
        if(tree->data[i] == tree->data[i-1])rle_count++;
        else break;
    }

    int output_size;
    if(rle_count < 32){
        compressed_buf[output_location++] = (rle_count)|(type<<5);
        output_size=1;
    } else {
        compressed_buf[output_location++] = 0xE0 | (type << 2) | (rle_count>>8);
        compressed_buf[output_location++] = rle_count&0xFF;
        output_size=2;
    }

    compressed_buf[output_location++] = tree->data[node->parent->uncompressed_size];
    output_size++;
    return output_size;
}

int output_compression_2byte_rle(compress_tree *tree, compress_node *node,uint8 *compressed_buf){
    int type=node->type;
    int uncompressed_location = node->parent->uncompressed_size;
    int output_location = node->parent->compressed_size;

    int rle_count=0;
    int i;
    for(i = uncompressed_location+3;i < tree->data_size;i+=2){
        if((tree->data[i] == tree->data[i-2]) &&
            (tree->data[i-1] == tree->data[i-3]))rle_count++;
        else break;
    }

    int output_size;
    if(rle_count < 32){
        compressed_buf[output_location++] = (rle_count)|(type << 5);
        output_size=1;
    } else {
        compressed_buf[output_location++] = 0xE0 | (type << 2) | (rle_count>>8);
        compressed_buf[output_location++] = rle_count&0xFF;
        output_size=2;
    }

    compressed_buf[output_location++] = tree->data[node->parent->uncompressed_size];
    compressed_buf[output_location++] = tree->data[node->parent->uncompressed_size+1];
    output_size+=2;
    return output_size;
}

int output_compression_rle_increment(compress_tree *tree, compress_node *node,uint8 *compressed_buf){
    int type=node->type;
    int uncompressed_location = node->parent->uncompressed_size;
    int output_location = node->parent->compressed_size;

    int rle_count=0;
    int i;
    for(i = uncompressed_location+1;i < tree->data_size;i++){
        if(tree->data[i] == (tree->data[i-1]-1))rle_count++;
        else break;
    }

    int output_size;
    if(rle_count < 32){
        compressed_buf[output_location++] = (rle_count)|(type << 5);
        output_size=1;
    } else {
        compressed_buf[output_location++] = 0xE0 | (type << 2) |(rle_count>>8);
        compressed_buf[output_location++] = rle_count&0xFF;
        output_size=2;
    }

    compressed_buf[output_location++] = tree->data[node->parent->uncompressed_size];
    output_size++;
    return output_size;
}

int output_compression_lz(compress_tree *tree, compress_node *node,uint8 *compressed_buf){
    int type=node->type;
    int u_size, c_size, lz_location,lz_length;
    int output_location = node->parent->compressed_size;

    //we need to give this the parent node so it can recalculate the lz compression
    do_lz_compress(tree, node->parent, &u_size,&c_size,&lz_location,&lz_length,LZ_TYPE_NORMAL);

    
    int output_size=0;
    if(lz_length < 32){
        compressed_buf[output_location++] = lz_length|(type << 5);
        output_size++;
    }
    else {
        compressed_buf[output_location++] = 0xE0 | (type << 2) | (lz_length>>8);
        compressed_buf[output_location++] = (lz_length)&0xFF;
        output_size+=2;
    }

    compressed_buf[output_location++] = (lz_location>>8)&0xFF;
    compressed_buf[output_location++] = lz_location&0xFF;

    output_size+=2;
    return output_size;
}

int output_compression_lz_bitreversed(compress_tree *tree, compress_node *node,uint8 *compressed_buf){
    int type=node->type;
    int u_size, c_size, lz_location,lz_length;
    int output_location = node->parent->compressed_size;

    do_lz_compress(tree, node->parent, &u_size,&c_size,&lz_location,&lz_length,LZ_TYPE_BITREVERSED);
 
    int output_size=0;
    if(lz_length < 32){
        compressed_buf[output_location++] = lz_length|(type << 5);
        output_size++;
    }
    else {
        compressed_buf[output_location++] = 0xE0 | (type << 2) | (lz_length>>8);
        compressed_buf[output_location++] = (lz_length)&0xFF;
        output_size+=2;
    }

    compressed_buf[output_location++] = (lz_location>>8)&0xFF;
    compressed_buf[output_location++] = lz_location&0xFF;

    output_size+=2;
    return output_size;
}

int output_compression_lz_reversed(compress_tree *tree, compress_node *node,uint8 *compressed_buf){
    int type=node->type;
    int u_size, c_size, lz_location,lz_length;
    int output_location = node->parent->compressed_size;

    do_lz_compress(tree, node->parent, &u_size,&c_size,&lz_location,&lz_length,LZ_TYPE_REVERSED);
 
    int output_size=0;
    if(lz_length < 32){
        compressed_buf[output_location++] = lz_length|(type << 5);
        output_size++;
    }
    else {
        compressed_buf[output_location++] = 0xE0 | (type << 2) | (lz_length>>8);
        compressed_buf[output_location++] = (lz_length)&0xFF;
        output_size+=2;
    }

    compressed_buf[output_location++] = (lz_location>>8)&0xFF;
    compressed_buf[output_location++] = lz_location&0xFF;

    output_size+=2;
    return output_size;
}

